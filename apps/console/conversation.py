"""Server-side LLM conversation loop whose only tools are Helios MCP tools."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import anyio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from helios_core import audit
from helios_core.authz import Principal
from helios_core.delegation import issue_assertion
from helios_core.llm import LLMClient, LLMError, llm_from_env


class ConversationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MCPClientConfig:
    url: str
    token: str
    delegation_secret: str
    timeout_seconds: float = 45.0

    @classmethod
    def from_env(cls) -> "MCPClientConfig":
        url = os.environ.get("HELIOS_MCP_URL", "").strip()
        token = os.environ.get("HELIOS_MCP_TOKEN", "").strip()
        secret = os.environ.get("HELIOS_MCP_DELEGATION_SECRET", "")
        if not url or not token or not secret:
            raise ConversationUnavailable(
                "Helios MCP conversation connectivity is not configured"
            )
        if not url.startswith("https://") and not url.startswith(
            "http://127.0.0.1"
        ):
            raise ConversationUnavailable(
                "HELIOS_MCP_URL must use HTTPS outside local development"
            )
        return cls(url, token, secret)


class ConversationService:
    def __init__(
        self,
        llm: LLMClient,
        mcp: MCPClientConfig,
        *,
        max_tool_rounds: int = 6,
        max_result_chars: int = 50_000,
    ):
        self.llm = llm
        self.mcp = mcp
        self.max_tool_rounds = max_tool_rounds
        self.max_result_chars = max_result_chars

    @classmethod
    def from_env(cls) -> "ConversationService":
        llm = llm_from_env()
        if llm is None:
            raise ConversationUnavailable(
                "The conversation LLM is not configured"
            )
        return cls(llm, MCPClientConfig.from_env())

    async def turn(
        self,
        principal: Principal,
        organization_id: str,
        model_id: str,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        try:
            context = audit.current_context()
            assertion = issue_assertion(
                self.mcp.delegation_secret,
                principal,
                organization_id,
                model_id,
                request_id=context.request_id if context else None,
                session_id=context.session_id if context else None,
            )
            headers = {
                "Authorization": f"Bearer {self.mcp.token}",
                "X-Helios-Principal-Assertion": assertion,
            }
            timeout = httpx2.Timeout(self.mcp.timeout_seconds)
            async with httpx2.AsyncClient(
                headers=headers, timeout=timeout
            ) as http_client:
                async with streamable_http_client(
                    self.mcp.url, http_client=http_client
                ) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        tools = [
                            {
                                "name": tool.name,
                                "description": tool.description or "",
                                "inputSchema": getattr(
                                    tool,
                                    "input_schema",
                                    getattr(tool, "inputSchema", {}),
                                ),
                            }
                            for tool in listed.tools
                        ]
                        return await self._tool_loop(
                            session,
                            tools,
                            model_id,
                            message,
                            history=history,
                        )
        except ConversationUnavailable:
            raise
        except Exception as exc:
            raise ConversationUnavailable(
                "The Helios MCP service is unavailable"
            ) from exc

    async def _tool_loop(
        self,
        session: ClientSession,
        tools: list[dict[str, Any]],
        model_id: str,
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        tool_by_name = {tool["name"]: tool for tool in tools}
        messages: list[dict[str, Any]] = [
            *_bounded_history(history or []),
            {"role": "user", "content": message}
        ]
        trace: list[dict[str, Any]] = []
        non_retryable_failures: set[str] = set()
        system = (
            "You are the Helios data assistant. Use only the supplied Helios "
            "MCP tools for semantic metadata, compilation, lineage, and data. "
            f"The authorized model is {model_id!r}; never request another "
            "model. Explain results accurately and do not invent data."
        )
        for _ in range(self.max_tool_rounds):
            try:
                turn = await anyio.to_thread.run_sync(
                    lambda: self.llm.tool_turn(system, messages, tools)
                )
            except LLMError as exc:
                raise ConversationUnavailable(
                    "The conversation LLM is unavailable"
                ) from exc
            if not turn.tool_calls:
                if not turn.text.strip():
                    raise ConversationUnavailable(
                        "The conversation LLM returned no answer"
                    )
                return {
                    "model_id": model_id,
                    "answer": turn.text,
                    "tool_trace": trace,
                    "query_result": _last_query_result(trace),
                }
            calls = [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
                for call in turn.tool_calls
            ]
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.text,
                    "tool_calls": calls,
                }
            )
            for call in turn.tool_calls:
                tool = tool_by_name.get(call.name)
                trace_arguments = call.arguments
                if tool is None:
                    result: Any = {
                        "error": "unknown_tool",
                        "message": "The LLM requested an unavailable tool",
                    }
                else:
                    arguments = dict(call.arguments)
                    properties = tool.get("inputSchema", {}).get(
                        "properties", {}
                    )
                    if "model" in properties:
                        arguments["model"] = model_id
                    trace_arguments = arguments
                    response = await session.call_tool(
                        call.name, arguments=arguments
                    )
                    result = _tool_result(response)
                encoded = json.dumps(result, default=str)
                if len(encoded) > self.max_result_chars:
                    result = {
                        "error": "tool_result_too_large",
                        "message": "The MCP tool result exceeded the safe limit",
                        "retryable": False,
                    }
                    encoded = json.dumps(result)
                repeated_failure = False
                if (
                    isinstance(result, dict)
                    and result.get("error")
                    and result.get("retryable") is not True
                ):
                    failure_key = json.dumps(
                        {
                            "tool": call.name,
                            "arguments": _safe_arguments(trace_arguments),
                            "error": result.get("error"),
                        },
                        sort_keys=True,
                        default=str,
                    )
                    repeated_failure = failure_key in non_retryable_failures
                    non_retryable_failures.add(failure_key)
                trace.append(
                    {
                        "tool": call.name,
                        "arguments": _safe_arguments(trace_arguments),
                        "result": result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": encoded,
                    }
                )
                if repeated_failure:
                    detail = result.get("message")
                    answer = (
                        str(detail)
                        if detail
                        else "Helios could not resolve that request "
                        "against the authorized semantic model."
                    )
                    return {
                        "model_id": model_id,
                        "answer": answer,
                        "tool_trace": trace,
                        "query_result": _last_query_result(trace),
                    }
        raise ConversationUnavailable(
            "The conversation exceeded the MCP tool-call limit"
        )


def _tool_result(response: Any) -> Any:
    structured = getattr(response, "structured_content", None)
    if structured is None:
        structured = getattr(response, "structuredContent", None)
    if structured is not None:
        if (
            isinstance(structured, dict)
            and set(structured) == {"result"}
        ):
            return structured["result"]
        return structured
    texts = [
        item.text
        for item in getattr(response, "content", [])
        if getattr(item, "type", None) == "text"
    ]
    text = "\n".join(texts)
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {"text": text}


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in arguments.items()
        if key.lower() not in {"token", "password", "secret", "api_key"}
    }


def _last_query_result(trace: list[dict[str, Any]]) -> dict | None:
    for item in reversed(trace):
        result = item.get("result")
        if (
            item.get("tool") == "run_query"
            and isinstance(result, dict)
            and "columns" in result
            and "rows" in result
        ):
            return {
                "columns": result["columns"],
                "rows": result["rows"],
                "sql": result.get("sql"),
            }
    return None


def _bounded_history(
    history: list[dict[str, str]],
    *,
    max_messages: int = 20,
    max_characters: int = 40_000,
) -> list[dict[str, str]]:
    accepted = [
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
        and item["content"].strip()
    ]
    bounded: list[dict[str, str]] = []
    characters = 0
    for item in reversed(accepted[-max_messages:]):
        size = len(item["content"])
        if bounded and characters + size > max_characters:
            break
        if size > max_characters:
            item = {
                "role": item["role"],
                "content": item["content"][-max_characters:],
            }
            size = len(item["content"])
        bounded.append(item)
        characters += size
    bounded.reverse()
    return bounded
