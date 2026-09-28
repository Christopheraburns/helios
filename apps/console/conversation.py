"""Server-side LLM conversation loop whose only tools are Helios MCP tools."""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from dataclasses import dataclass
from typing import Any

import anyio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from helios_core import __version__, audit
from helios_core.authz import Principal
from helios_core.delegation import issue_assertion
from helios_core.llm import (
    LLMClient,
    LLMError,
    LLMTimeoutError,
    llm_from_env,
)
from helios_core.metadata import MetadataRepository
from helios_core.tracing import TraceRecorder, utcnow

LOGGER = logging.getLogger(__name__)


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
        raw_timeout = os.environ.get("HELIOS_MCP_TIMEOUT_SECONDS", "45")
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
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise ConversationUnavailable(
                "HELIOS_MCP_TIMEOUT_SECONDS must be a positive number"
            ) from exc
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ConversationUnavailable(
                "HELIOS_MCP_TIMEOUT_SECONDS must be a positive number"
            )
        return cls(url, token, secret, timeout_seconds)


async def inspect_mcp(
    mcp: MCPClientConfig,
    principal: Principal,
    organization_id: str,
    model_id: str,
) -> dict[str, Any]:
    """Initialize the configured MCP service and return public capabilities."""
    context = audit.current_context()
    assertion = issue_assertion(
        mcp.delegation_secret,
        principal,
        organization_id,
        model_id,
        request_id=context.request_id if context else None,
        session_id=context.session_id if context else None,
    )
    headers = {
        "Authorization": f"Bearer {mcp.token}",
        "X-Helios-Principal-Assertion": assertion,
    }
    # A management-page health probe should fail quickly even when the
    # conversation timeout is deliberately long.
    timeout = httpx2.Timeout(min(mcp.timeout_seconds, 10.0))
    async with httpx2.AsyncClient(headers=headers, timeout=timeout) as client:
        async with streamable_http_client(
            mcp.url,
            http_client=client,
        ) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                return {
                    "server": _mcp_provenance(initialized),
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description or "",
                        }
                        for tool in listed.tools
                    ],
                }


class ConversationService:
    def __init__(
        self,
        llm: LLMClient,
        mcp: MCPClientConfig,
        *,
        max_tool_rounds: int = 6,
        max_result_chars: int = 50_000,
        trace_repository: MetadataRepository | None = None,
    ):
        self.llm = llm
        self.provider_timeout_seconds = float(
            getattr(llm, "timeout", 30.0)
        )
        self.mcp = mcp
        self.max_tool_rounds = max_tool_rounds
        self.max_result_chars = max_result_chars
        self.trace_repository = trace_repository

    @classmethod
    def from_env(
        cls,
        *,
        max_tool_rounds: int = 6,
        trace_repository: MetadataRepository | None = None,
    ) -> "ConversationService":
        llm = llm_from_env()
        if llm is None:
            raise ConversationUnavailable(
                "The conversation LLM is not configured"
            )
        return cls(
            llm,
            MCPClientConfig.from_env(),
            max_tool_rounds=max_tool_rounds,
            trace_repository=trace_repository,
        )

    async def turn(
        self,
        principal: Principal,
        organization_id: str,
        model_id: str,
        message: str,
        history: list[dict[str, str]] | None = None,
        *,
        purpose: str = "conversation",
        question_id: str | None = None,
        trace_run_id: str | None = None,
    ) -> dict[str, Any]:
        context = audit.current_context()
        recorder = (
            TraceRecorder.start(
                self.trace_repository,
                principal_id=principal.id,
                organization_id=organization_id,
                model_id=model_id,
                question=message,
                provider=self.llm.provider,
                llm_model=self.llm.model,
                request_id=context.request_id if context else None,
                purpose=purpose,
                question_id=question_id,
                run_id=trace_run_id,
            )
            if self.trace_repository is not None
            else None
        )
        try:
            assertion = issue_assertion(
                self.mcp.delegation_secret,
                principal,
                organization_id,
                model_id,
                request_id=context.request_id if context else None,
                session_id=context.session_id if context else None,
                trace_run_id=recorder.run.id if recorder else None,
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
                        initialized = await session.initialize()
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
                            provenance={
                                "helios": {"api_version": __version__},
                                "llm": {
                                    "provider": self.llm.provider,
                                    "model": self.llm.model,
                                },
                                "mcp": _mcp_provenance(initialized),
                            },
                            request_id=(
                                context.request_id if context else None
                            ),
                            recorder=recorder,
                        )
        except ConversationUnavailable as exc:
            if recorder and not recorder.finished:
                recorder.finish(
                    status="failed",
                    termination_reason="unavailable",
                    answer=str(exc),
                    tokens_in=0,
                    tokens_out=0,
                )
            raise
        except Exception as exc:
            if recorder and not recorder.finished:
                recorder.finish(
                    status="failed",
                    termination_reason="crashed",
                    answer=None,
                    tokens_in=0,
                    tokens_out=0,
                )
            unavailable = _nested_exception(exc, ConversationUnavailable)
            if unavailable is not None:
                raise ConversationUnavailable(str(unavailable)) from exc
            LOGGER.exception("unexpected MCP conversation failure")
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
        provenance: dict[str, Any] | None = None,
        request_id: str | None = None,
        recorder: TraceRecorder | None = None,
    ) -> dict[str, Any]:
        tool_by_name = {tool["name"]: tool for tool in tools}
        messages: list[dict[str, Any]] = [
            *_bounded_history(history or []),
            {"role": "user", "content": message}
        ]
        trace: list[dict[str, Any]] = []
        non_retryable_failures: set[str] = set()
        unresolved_tool_error: dict[str, Any] | None = None
        semantic_failure_count = 0
        tokens_in = 0
        tokens_out = 0
        system = (
            "You are the Helios data assistant. Use only the supplied Helios "
            "MCP tools for semantic metadata, compilation, lineage, and data. "
            f"The authorized model is {model_id!r}; never request another "
            "model. Before compiling or running a query, call "
            "search_semantics and copy metric names exactly from its results. "
            "Dimensions and filter columns must use exact "
            "database.table.column identifiers returned by search_semantics "
            "or describe. Never invent, shorten, or convert semantic names. "
            "If a tool reports an invalid semantic query, search again and "
            "correct the identifiers. Never label semantic validation, "
            "authorization, or tool-argument errors as an Impala outage. "
            "Explain results accurately and do not invent data."
        )

        def failure_result(result: dict[str, Any]) -> dict[str, Any]:
            code = str(result.get("error") or "tool_error")
            answer = _user_facing_tool_error(result)
            if recorder and not recorder.finished:
                recorder.finish(
                    status="failed",
                    termination_reason=code,
                    answer=answer,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )
            return {
                "model_id": model_id,
                "answer": answer,
                "failure": {
                    "code": code,
                    "message": answer,
                    "retryable": result.get("retryable") is True,
                },
                "tool_trace": trace,
                "query_result": _last_query_result(trace),
                "provenance": provenance or {},
                "request_id": request_id,
                "trace_run_id": recorder.run.id if recorder else None,
            }

        for round_index in range(self.max_tool_rounds):
            llm_started = utcnow()
            try:
                with anyio.fail_after(self.provider_timeout_seconds):
                    turn = await anyio.to_thread.run_sync(
                        lambda: self.llm.tool_turn(system, messages, tools),
                        abandon_on_cancel=True,
                    )
            except (LLMTimeoutError, TimeoutError) as exc:
                message = (
                    "The model provider took too long to respond, so this "
                    "request was ended. Please try again or check the model "
                    "provider connection."
                )
                if recorder:
                    recorder.span(
                        component="agent",
                        kind="llm",
                        name=f"LLM round {round_index + 1}",
                        status="error",
                        started_at=llm_started,
                        completed_at=utcnow(),
                        input={
                            "system": system,
                            "messages": messages,
                            "tools": tools,
                        },
                        error=str(exc) or message,
                        attributes={"round": round_index + 1},
                    )
                    recorder.finish(
                        status="failed",
                        termination_reason="timeout",
                        answer=message,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                raise ConversationUnavailable(message) from exc
            except LLMError as exc:
                if recorder:
                    recorder.span(
                        component="agent",
                        kind="llm",
                        name=f"LLM round {round_index + 1}",
                        status="error",
                        started_at=llm_started,
                        completed_at=utcnow(),
                        input={
                            "system": system,
                            "messages": messages,
                            "tools": tools,
                        },
                        error=str(exc),
                        attributes={"round": round_index + 1},
                    )
                    recorder.finish(
                        status="failed",
                        termination_reason="llm_error",
                        answer=None,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                raise ConversationUnavailable(
                    "The conversation LLM is unavailable"
                ) from exc
            tokens_in += turn.tokens_in
            tokens_out += turn.tokens_out
            if recorder:
                recorder.span(
                    component="agent",
                    kind="llm",
                    name=f"LLM round {round_index + 1}",
                    status="success",
                    started_at=llm_started,
                    completed_at=utcnow(),
                    input={
                        "system": system,
                        "messages": messages,
                        "tools": tools,
                    },
                    output={
                        "text": turn.text,
                        "raw_tool_calls": list(turn.raw_tool_calls),
                        "parsed_tool_calls": [
                            {
                                "id": call.id,
                                "name": call.name,
                                "arguments": call.arguments,
                                "raw_arguments": call.raw_arguments,
                                "parse_error": call.parse_error,
                            }
                            for call in turn.tool_calls
                        ],
                    },
                    attributes={
                        "round": round_index + 1,
                        "stop_reason": turn.stop_reason,
                        "tokens_in": turn.tokens_in,
                        "tokens_out": turn.tokens_out,
                        "provider_latency_ms": turn.latency_ms,
                    },
                )
            if not turn.tool_calls:
                if unresolved_tool_error is not None:
                    return failure_result(unresolved_tool_error)
                if not turn.text.strip():
                    if recorder:
                        recorder.finish(
                            status="failed",
                            termination_reason="empty_answer",
                            answer=None,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                        )
                    raise ConversationUnavailable(
                        "The conversation LLM returned no answer"
                    )
                if recorder:
                    recorder.finish(
                        status="completed",
                        termination_reason="final_answer",
                        answer=turn.text,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                return {
                    "model_id": model_id,
                    "answer": turn.text,
                    "tool_trace": trace,
                    "query_result": _last_query_result(trace),
                    "provenance": provenance or {},
                    "request_id": request_id,
                    "trace_run_id": recorder.run.id if recorder else None,
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
                tool_started = utcnow()
                tool_span_id = str(uuid.uuid4())
                tool_sequence = recorder.next_sequence() if recorder else None
                tool = tool_by_name.get(call.name)
                trace_arguments = call.arguments
                tool_error: Exception | None = None
                try:
                    if call.parse_error:
                        result = {
                            "error": "invalid_tool_arguments",
                            "message": call.parse_error,
                            "retryable": False,
                        }
                    elif tool is None:
                        result = {
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
                        if recorder:
                            response = await session.call_tool(
                                call.name,
                                arguments=arguments,
                                meta={
                                    "helios_trace_run_id": recorder.run.id,
                                    "helios_parent_span_id": tool_span_id,
                                    "helios_sequence": tool_sequence or 0,
                                },
                            )
                        else:
                            response = await session.call_tool(
                                call.name,
                                arguments=arguments,
                            )
                        result = _tool_result(response)
                except Exception as exc:
                    tool_error = exc
                    result = {
                        "error": "mcp_tool_call_failed",
                        "message": "The MCP tool call failed",
                        "retryable": True,
                    }
                if isinstance(result, dict) and result.get("error"):
                    error_code = str(result["error"])
                    if error_code in _SEMANTIC_ERROR_CODES:
                        semantic_failure_count += 1
                        result = {
                            **result,
                            "recovery": (
                                "Call search_semantics again. Copy metric "
                                "names exactly and use full "
                                "database.table.column field identifiers "
                                "from the tool results before retrying."
                            ),
                        }
                    unresolved_tool_error = result
                elif call.name in {"compile_query", "run_query"}:
                    unresolved_tool_error = None
                    semantic_failure_count = 0
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
                if recorder:
                    recorder.span(
                        component="agent",
                        kind="tool",
                        name=call.name,
                        status="error" if tool_error or (
                            isinstance(result, dict) and result.get("error")
                        ) else "success",
                        started_at=tool_started,
                        completed_at=utcnow(),
                        input={
                            "raw_arguments": call.raw_arguments,
                            "parsed_arguments": trace_arguments,
                        },
                        output=result,
                        attributes={
                            "round": round_index + 1,
                            "tool_call_id": call.id,
                            "result_size_bytes": len(encoded.encode()),
                            "args_valid": not bool(call.parse_error),
                            "repeated_failure": repeated_failure,
                        },
                        error=(
                            str(tool_error)
                            if tool_error
                            else (
                                str(result.get("message") or result.get("error"))
                                if isinstance(result, dict) and result.get("error")
                                else None
                            )
                        ),
                        sequence=tool_sequence,
                        span_id=tool_span_id,
                    )
                if tool_error:
                    if recorder:
                        recorder.finish(
                            status="failed",
                            termination_reason="mcp_tool_error",
                            answer=None,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                        )
                    raise tool_error
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": encoded,
                    }
                )
                if (
                    isinstance(result, dict)
                    and result.get("error") in _SEMANTIC_ERROR_CODES
                    and semantic_failure_count >= 2
                ):
                    return failure_result(result)
                if repeated_failure:
                    return failure_result(result)
        if unresolved_tool_error is not None:
            return failure_result(unresolved_tool_error)
        if recorder:
            recorder.finish(
                status="failed",
                termination_reason="tool_round_limit",
                answer=None,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        raise ConversationUnavailable(
            "The conversation exceeded the MCP tool-call limit"
        )


_SEMANTIC_ERROR_CODES = frozenset({
    "invalid_semantic_query",
    "invalid_semantic_reference",
    "semantic_not_found",
})
_ACCESS_ERROR_CODES = frozenset({
    "data_authorization_denied",
    "proxy_delegation_denied",
    "query_denied",
})
_IMPALA_AUTH_ERROR_CODES = frozenset({
    "impala_authentication_failed",
    "workload_authentication_failed",
})
_IMPALA_SERVICE_ERROR_CODES = frozenset({
    "impala_unavailable",
    "query_unavailable",
})


def _user_facing_tool_error(result: dict[str, Any]) -> str:
    code = str(result.get("error") or "tool_error")
    detail = str(result.get("message") or "").strip()
    if code in _SEMANTIC_ERROR_CODES:
        fallback = (
            "The requested metric or field is not available in the "
            "authorized semantic model."
        )
        return f"Semantic model issue: {detail or fallback}"
    if code in _ACCESS_ERROR_CODES:
        fallback = (
            "The current user is not authorized to run the requested query."
        )
        return f"Data access issue: {detail or fallback}"
    if code in _IMPALA_AUTH_ERROR_CODES:
        fallback = "Impala authentication could not be completed."
        return f"Impala authentication issue: {detail or fallback}"
    if code in _IMPALA_SERVICE_ERROR_CODES:
        fallback = "The Impala query service is temporarily unavailable."
        return f"Impala service issue: {detail or fallback}"
    if code in {"invalid_tool_arguments", "unknown_tool"}:
        fallback = "The model produced an invalid MCP tool request."
        return f"Model tool-call issue: {detail or fallback}"
    return f"MCP tool issue: {detail or 'The requested operation failed.'}"


def _mcp_provenance(initialized: Any) -> dict[str, Any]:
    server_info = getattr(
        initialized,
        "serverInfo",
        getattr(initialized, "server_info", None),
    )
    return {
        "server_name": getattr(server_info, "name", None),
        "server_version": getattr(server_info, "version", None),
        "protocol_version": getattr(
            initialized,
            "protocolVersion",
            getattr(initialized, "protocol_version", None),
        ),
    }


def _nested_exception(
    exception: BaseException,
    expected: type[ConversationUnavailable],
) -> ConversationUnavailable | None:
    if isinstance(exception, expected):
        return exception
    for nested in getattr(exception, "exceptions", ()):
        matched = _nested_exception(nested, expected)
        if matched is not None:
            return matched
    cause = exception.__cause__ or exception.__context__
    if cause is not None and cause is not exception:
        return _nested_exception(cause, expected)
    return None


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
