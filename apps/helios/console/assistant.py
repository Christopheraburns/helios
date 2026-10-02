"""Navigation assistant: server-computed workspace state plus an LLM narrator.

    GET  /api/v1/assistant/workspace-state   deterministic state and journey checklists
    POST /api/v1/assistant/turn              one assistant turn (read-only tools)

The LLM narrates; the code in ``journeys.py`` decides what is done.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Annotated, Any, Callable, Literal
from urllib.parse import parse_qsl

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from helios_core import audit, authz
from helios_core.domain import Model, Organization
from helios_core.index import runs as index_runs
from helios_core.llm import LLMClient, LLMError, LLMTimeoutError, llm_from_env
from helios_core.metadata import MetadataRepository
from helios_core.tracing import TraceRecorder, utcnow
from apps.helios.console import ontology
from apps.helios.console.api import (
    AuthorizedModel,
    _model_overview,
    _model_provider_store,
    _require,
    accessible_models,
    authorization_policy,
    current_principal,
    graph_repository,
    resource_store,
)
from apps.helios.console.conversation import (
    ConversationUnavailable,
    MCPClientConfig,
    _bounded_history,
)
from apps.helios.console.journeys import JOURNEYS, evaluate, selected_model
from apps.helios.console.model_provider import (
    environment_provider_summary,
    llm_for_settings,
)

LOGGER = logging.getLogger(__name__)
assistant_router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

REPO_ROOT = Path(__file__).resolve().parents[3]
MAX_HISTORY_MESSAGES = 12
MAX_EXCERPT_CHARS = 600
MAX_DOC_RESULTS = 5


class AssistantUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- route catalog

ROUTE_CATALOG: dict[str, tuple[str, str]] = {
    "/home": (
        "Home",
        "Landing page: pick an organization and model and see what to do next.",
    ),
    "/talk": (
        "Talk to Your Data",
        "Ask plain-language questions of a published semantic model through the MCP server.",
    ),
    "/model-overview": (
        "Model overview",
        "Lifecycle, counts, data sources and publish status of the selected model.",
    ),
    "/canvas": (
        "Canvas",
        "Graph of the selected model; lenses physical, semantic and ontology (?lens=); "
        "review mode via ?review_run_id=<run id>.",
    ),
    "/models": (
        "Discovery runs",
        "Discovery runs (harvest, profile, propose) for the selected model.",
    ),
    "/models/runs/{runId}": (
        "Discovery run",
        "One discovery run: stages, artifacts, warnings and errors.",
    ),
    "/data-sources": (
        "Data sources",
        "Register warehouse and document sources; crawl settings live on each source.",
    ),
    "/ontology": (
        "Ontology",
        "Published ontology versions: check, publish, activate and browse classes.",
    ),
    "/crawler": (
        "Crawler",
        "Crawl runs (tab runs) and crawler settings (tab settings).",
    ),
    "/governance": ("Governance", "Glossary terms of the selected model."),
    "/governance/proposals": (
        "Proposed terms",
        "Governance tab 'Proposed terms': glossary terms proposed by discovery.",
    ),
    "/governance/model-provider": (
        "Model provider",
        "LLM provider and key for this browser session.",
    ),
    "/governance/mcp": (
        "MCP server",
        "MCP server connection status and tool-round settings.",
    ),
    "/activity": ("Activity", "Audit events and agent trace runs."),
    "/docs": ("Docs", "Documentation browser; a page lives at /docs/helios/<slug>."),
}

VOCABULARY: dict[str, str] = {
    "semantic model": (
        "The published description of what tables and columns mean: datasets, "
        "fields, relationships and metrics."
    ),
    "ontology": (
        "A versioned class hierarchy (and claim predicates) used to classify "
        "unstructured documents; one version is active."
    ),
    "glossary": (
        "Business terms, managed in Governance and optionally synced to Atlas; "
        "discovery proposes new ones."
    ),
    "canvas": "The graph view of a model; nodes are datasets, fields, metrics and concepts.",
    "lens": (
        "A canvas view: physical (tables and columns), semantic (datasets and "
        "metrics) or ontology (classes)."
    ),
    "proposal": (
        "Something discovery suggested (dataset, field role, relationship, metric, "
        "term) that a person accepts or rejects in review."
    ),
    "Ossie": "The open semantic file format Helios publishes a model in.",
    "MCP": (
        "Model Context Protocol; the Helios MCP server exposes the published model "
        "to Talk to Your Data and external agents."
    ),
}


def _route_pattern(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\{[^/]+\}", r"[^/]+", template) + "$")


_ROUTE_PATTERNS = {route: _route_pattern(route) for route in ROUTE_CATALOG}


def resolve_route(route: str) -> tuple[str, dict[str, str]] | None:
    """The catalog entry a route belongs to, plus any query parameters it carried."""
    path, _, query = route.strip().partition("?")
    path = path.rstrip("/") or "/"
    params = dict(parse_qsl(query)) if query else {}
    if path.startswith("/docs/helios/"):
        if path.removeprefix("/docs/helios/") in {slug for _, slug, _ in DOC_SOURCES}:
            return path, params
        return None
    if any(pattern.match(path) for pattern in _ROUTE_PATTERNS.values()):
        return path, params
    return None


def _route_label(path: str) -> str:
    for template, pattern in _ROUTE_PATTERNS.items():
        if pattern.match(path):
            return f"Open {ROUTE_CATALOG[template][0]}"
    return "Open docs"


# ---------------------------------------------------------------- docs search

DOC_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("README.md", "overview", "Helios Query (overview)"),
    ("docs/helios-walkthrough.md", "walkthrough", "End-to-end walkthrough"),
    ("docs/console-propose.md", "console-propose", "Reading a proposal"),
    ("docs/console-review.md", "console-review", "Reviewing and publishing"),
    ("docs/console-runs.md", "console-runs", "Reading a discovery run"),
    ("docs/mcp-server.md", "mcp-server", "Helios MCP server"),
    ("docs/SECURITY_ARCHITECTURE.md", "security", "Identity and authorization"),
    ("docs/ui-deployment.md", "ui-deployment", "API and UI deployment"),
    ("docs/ontology-deployment.md", "ontology-deployment", "Ontology deployment"),
)

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_MERMAID = re.compile(r"```mermaid.*?```", re.DOTALL)
_HEADING = re.compile(r"^(#{2,3})\s+(.*)$", re.MULTILINE)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "of", "on", "or", "the", "this",
    "to", "what", "when", "where", "which", "why", "with", "you",
})
_doc_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _stem(token: str) -> str:
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _terms(text: str) -> list[str]:
    return [
        _stem(token)
        for token in _TOKEN.findall(text.lower())
        if token not in _STOPWORDS and len(token) > 1
    ]


def _chunks(slug: str, title: str, text: str) -> list[dict[str, Any]]:
    text = _MERMAID.sub("", _FRONTMATTER.sub("", text))
    chunks: list[dict[str, Any]] = []
    position = 0
    heading = ""
    for match in _HEADING.finditer(text):
        chunks.append((heading, text[position:match.start()]))
        heading = match.group(2).strip()
        position = match.end()
    chunks.append((heading, text[position:]))
    results = []
    for heading, body in chunks:
        body = " ".join(body.split())
        if not body:
            continue
        results.append({
            "slug": slug,
            "title": title,
            "heading": heading,
            "body": body,
            "title_terms": set(_terms(title)),
            "heading_terms": set(_terms(heading)),
            "body_terms": _terms(body),
        })
    return results


def _doc_chunks() -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for relative, slug, title in DOC_SOURCES:
        path = REPO_ROOT / relative
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        cached = _doc_cache.get(slug)
        if cached is None or cached[0] != mtime:
            try:
                cached = (mtime, _chunks(slug, title, path.read_text()))
            except OSError:
                continue
            _doc_cache[slug] = cached
        chunks.extend(cached[1])
    return chunks


def search_docs(query: str, limit: int = MAX_DOC_RESULTS) -> list[dict[str, Any]]:
    terms = set(_terms(query))
    if not terms:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for chunk in _doc_chunks():
        score = 0.0
        for term in terms:
            if term in chunk["heading_terms"]:
                score += 4
            if term in chunk["title_terms"]:
                score += 3
            score += min(chunk["body_terms"].count(term), 5) * 0.5
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "slug": chunk["slug"],
            "title": (
                f"{chunk['title']} - {chunk['heading']}"
                if chunk["heading"]
                else chunk["title"]
            ),
            "route": f"/docs/helios/{chunk['slug']}",
            "excerpt": chunk["body"][:MAX_EXCERPT_CHARS],
        }
        for _, chunk in scored[:limit]
    ]


# ---------------------------------------------------------------- workspace state

def _llm_provider_state(request: Request, principal: authz.Principal) -> dict[str, Any]:
    session_id = audit.normalize_correlation_id(
        request.headers.get("x-helios-session-id")
    )
    settings = (
        _model_provider_store(request).get(principal.id, session_id)
        if session_id is not None
        else None
    )
    if settings is not None:
        return {"configured": True, "provider": settings.provider, "model": settings.model}
    provider, model = environment_provider_summary()
    return {"configured": provider is not None, "provider": provider, "model": model}


def _mcp_state() -> dict[str, Any]:
    try:
        MCPClientConfig.from_env()
    except ConversationUnavailable:
        return {"configured": False}
    return {"configured": True}


def _ontology_state(request: Request) -> dict[str, Any]:
    try:
        versions = ontology.list_ontology_versions(request)
    except Exception:  # noqa: BLE001 - the index may be unreachable
        LOGGER.warning("ontology versions unavailable for workspace state", exc_info=True)
        return {"version_count": 0, "active_version": None}
    active = next((v["version"] for v in versions if v.get("is_active")), None)
    return {"version_count": len(versions), "active_version": active}


def _crawl_runs() -> list[Any]:
    try:
        index = ontology.index_store()
        return index_runs.runs(index) if index is not None else []
    except Exception:  # noqa: BLE001 - the index may be unreachable
        LOGGER.warning("crawl runs unavailable for workspace state", exc_info=True)
        return []


def _data_sources_state(
    repository: Any, organization_id: str, crawl_runs: list[Any]
) -> list[dict[str, Any]]:
    lister = getattr(repository, "data_sources_for_organization", None)
    sources = lister(organization_id) if callable(lister) else []
    last_by_source: dict[str, Any] = {}
    for run in crawl_runs:
        last_by_source.setdefault(run.source, run)
    views = []
    for source in sources:
        last = last_by_source.get(source.id)
        views.append({
            "id": source.id,
            "name": source.name,
            "connector": source.connector,
            "crawl_enabled": bool((source.crawl or {}).get("enabled")),
            "last_crawl": (
                {
                    "crawl_run_id": last.crawl_run_id,
                    "status": last.status,
                    "started_at": last.started_at,
                }
                if last is not None
                else None
            ),
        })
    return views


def _crawl_runs_state(crawl_runs: list[Any]) -> dict[str, Any]:
    last = crawl_runs[0] if crawl_runs else None
    return {
        "total": len(crawl_runs),
        "last": (
            {
                "crawl_run_id": last.crawl_run_id,
                "status": last.status,
                "started_at": last.started_at,
                "source": last.source,
            }
            if last is not None
            else None
        ),
    }


def _has_conversations(repository: Any, model_id: str, principal_id: str) -> bool:
    lister = getattr(repository, "conversations_for_principal", None)
    if not callable(lister):
        return False
    try:
        return bool(lister(model_id, principal_id))
    except Exception:  # noqa: BLE001 - never let history lookups fail the state
        LOGGER.warning("conversation lookup failed for %s", model_id, exc_info=True)
        return False


def _model_state(
    context: AuthorizedModel, repository: Any, graphs: Any
) -> dict[str, Any]:
    model = context.model
    try:
        overview = _model_overview(context, repository, graphs)
        lifecycle = overview["lifecycle"]
        summary = overview["summary"]
    except Exception:  # noqa: BLE001 - one bad model must not fail the endpoint
        LOGGER.warning("overview unavailable for model %s", model.id, exc_info=True)
        lifecycle = {
            "publication_state": "configured",
            "discovery_status": "unavailable",
            "review_status": "not_available",
            "unresolved_review_items": None,
            "latest_run_id": (
                model.discovery_run_ids[-1] if model.discovery_run_ids else None
            ),
        }
        summary = {
            "dataset_count": 0,
            "relationship_count": 0,
            "concept_count": 0,
            "metric_count": 0,
        }
    return {
        "id": model.id,
        "name": model.name,
        "data_source_count": len(model.data_sources),
        "lifecycle": lifecycle,
        "summary": summary,
        "has_conversations": _has_conversations(
            repository, model.id, context.principal.id
        ),
        "available_actions": context.available_actions,
    }


def _authorize_workspace(
    request: Request,
    principal: authz.Principal,
    policy: authz.Policy,
    organization_id: str,
) -> Organization:
    organization = resource_store(request).organization(organization_id)
    if organization is None:
        raise HTTPException(404, "organization not found")
    resource = authz.Resource(
        "organization", organization.id, organization_id=organization.id
    )
    if policy.can(principal, authz.Action.ORGANIZATION_READ, resource).allowed:
        return organization
    # Model-only principals (e.g. a model owner) still get a workspace view
    # limited to the models they can read.
    if accessible_models(
        request.app.state.metadata_repository, principal, policy, organization.id
    ):
        return organization
    _require(policy, principal, authz.Action.ORGANIZATION_READ, resource)
    return organization


def _selected_model(
    models: list[Model],
    model_id: str | None,
    principal: authz.Principal,
    policy: authz.Policy,
    repository: Any,
    organization: Organization,
) -> str | None:
    if model_id is None:
        return None
    if any(model.id == model_id for model in models):
        return model_id
    model = repository.model(model_id)
    if model is None or model.organization_id != organization.id:
        raise HTTPException(404, "model not found")
    _require(
        policy,
        principal,
        authz.Action.MODEL_READ,
        authz.Resource("model", model.id, model.organization_id),
    )
    return model_id


def build_workspace_state(
    request: Request,
    principal: authz.Principal,
    policy: authz.Policy,
    organization: Organization,
    model_id: str | None,
) -> dict[str, Any]:
    repository = request.app.state.metadata_repository
    graphs = graph_repository(request)
    models = accessible_models(repository, principal, policy, organization.id)
    models.sort(key=lambda model: model.name.lower())
    selected = _selected_model(
        models, model_id, principal, policy, repository, organization
    )
    crawl_runs = _crawl_runs()
    state: dict[str, Any] = {
        "organization": {"id": organization.id, "name": organization.name},
        "selected_model_id": selected,
        "llm_provider": _llm_provider_state(request, principal),
        "mcp": _mcp_state(),
        "ontology": _ontology_state(request),
        "data_sources": _data_sources_state(repository, organization.id, crawl_runs),
        "crawl_runs": _crawl_runs_state(crawl_runs),
        "models": [
            _model_state(AuthorizedModel(model, principal, policy), repository, graphs)
            for model in models
        ],
    }
    state["journeys"] = evaluate(JOURNEYS, state)
    return state


@assistant_router.get("/workspace-state")
def get_workspace_state(
    request: Request,
    organization: str,
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
    model: str | None = None,
) -> dict[str, Any]:
    org = _authorize_workspace(request, principal, policy, organization)
    return build_workspace_state(request, principal, policy, org, model)


# ---------------------------------------------------------------- assistant turn

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_workspace_state",
        "description": (
            "The current workspace: LLM and MCP configuration, data sources, "
            "ontology versions, crawl runs, models with lifecycle, and the "
            "journey checklists with each step's status. Call it before "
            "describing state."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_docs",
        "description": (
            "Search the Helios documentation for how-to and concept "
            "questions. Returns up to five passages with the docs route."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "navigate",
        "description": (
            "Suggest a page for the user to open; the UI shows it as a "
            "button. The route must come from the route catalog. Optional "
            "params are query parameters for that page (for example lens or "
            "review_run_id); label is the button text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "route": {"type": "string"},
                "params": {"type": "object"},
                "label": {"type": "string"},
            },
            "required": ["route"],
        },
    },
]


def _journey_summary(state: dict[str, Any]) -> str:
    lines = []
    for journey in state.get("journeys", []):
        progress = journey["progress"]
        lines.append(
            f"- {journey['title']} ({progress['done']}/{progress['total']} done; "
            f"next: {journey['next_step_id'] or 'none'})"
        )
        for step in journey["steps"]:
            lines.append(f"    {step['id']}: {step['status']}")
    return "\n".join(lines)


def build_system_prompt(
    state: dict[str, Any],
    organization_id: str,
    model_id: str | None,
    location: dict[str, Any] | None,
) -> str:
    routes = "\n".join(
        f"- {route}: {purpose}" for route, (_, purpose) in ROUTE_CATALOG.items()
    )
    vocabulary = "\n".join(f"- {term}: {meaning}" for term, meaning in VOCABULARY.items())
    model = selected_model(state)
    where = (
        f"The user is currently at {location.get('pathname')}{location.get('search') or ''}."
        if location and location.get("pathname")
        else "The user's current page is unknown."
    )
    return (
        "You are the Helios navigation assistant. Helios reads a data "
        "warehouse, proposes what its tables mean, lets a person review and "
        "publish a semantic model, and then answers questions over it. You "
        "help in two modes: guide a new user end to end through a journey, "
        "or help a user manage what already exists.\n\n"
        f"Organization: {organization_id}. Selected model: "
        f"{model_id or (model['id'] if model else 'none')}. {where}\n\n"
        "Route catalog (the only pages you may send the user to):\n"
        f"{routes}\n\n"
        f"Vocabulary:\n{vocabulary}\n\n"
        "Journey status right now (computed by the server, not by you):\n"
        f"{_journey_summary(state)}\n\n"
        "Rules:\n"
        "- Always call get_workspace_state before claiming anything about "
        "the workspace beyond the journey summary above.\n"
        "- Use search_docs for how or why questions and cite the page.\n"
        "- When you recommend a page, call navigate so the UI can offer a "
        "button; include query params the page needs.\n"
        "- If a step is external (runs outside the UI), say so plainly and "
        "give the step's note.\n"
        "- Be concise. Never invent state, runs, models or settings.\n"
        "- The journey status above is authoritative; do not contradict it."
    )


class AssistantService:
    def __init__(
        self,
        llm: LLMClient,
        *,
        max_tool_rounds: int = 6,
        trace_repository: MetadataRepository | None = None,
        max_result_chars: int = 50_000,
    ) -> None:
        self.llm = llm
        self.max_tool_rounds = max_tool_rounds
        self.trace_repository = trace_repository
        self.max_result_chars = max_result_chars
        self.provider_timeout_seconds = float(getattr(llm, "timeout", 30.0))
        self._tracing_disabled = False

    def _recorder(
        self,
        principal: authz.Principal,
        organization_id: str,
        model_id: str | None,
        message: str,
        request_id: str | None,
    ) -> TraceRecorder | None:
        if self.trace_repository is None or not model_id or self._tracing_disabled:
            return None
        try:
            return TraceRecorder.start(
                self.trace_repository,
                principal_id=principal.id,
                organization_id=organization_id,
                model_id=model_id,
                question=message,
                provider=self.llm.provider,
                llm_model=self.llm.model,
                request_id=request_id,
                purpose="assistant",
            )
        except Exception as exc:  # noqa: BLE001 - tracing is best effort
            # The metadata schema may not accept the "assistant" purpose yet.
            self._tracing_disabled = True
            LOGGER.warning("assistant tracing disabled: %s", exc)
            return None

    async def turn(
        self,
        principal: authz.Principal,
        organization_id: str,
        model_id: str | None,
        message: str,
        history: list[dict[str, str]] | None,
        location: dict[str, Any] | None,
        state_provider: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        context = audit.current_context()
        request_id = context.request_id if context else None
        state = state_provider()
        if model_id is None:
            model_id = state.get("selected_model_id")
        system = build_system_prompt(state, organization_id, model_id, location)
        messages: list[dict[str, Any]] = [
            *_bounded_history(history or [], max_messages=MAX_HISTORY_MESSAGES),
            {"role": "user", "content": message},
        ]
        actions: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        tokens_in = 0
        tokens_out = 0
        recorder = self._recorder(
            principal, organization_id, model_id, message, request_id
        )
        provenance = {"llm": {"provider": self.llm.provider, "model": self.llm.model}}

        def finish(status: str, reason: str, answer: str | None) -> None:
            if recorder and not recorder.finished:
                recorder.finish(
                    status=status,
                    termination_reason=reason,
                    answer=answer,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

        def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            if name == "get_workspace_state":
                return state
            if name == "search_docs":
                query = str(arguments.get("query") or "")
                return {"results": search_docs(query)}
            if name == "navigate":
                return self._navigate(arguments, organization_id, model_id, actions)
            return {"ok": False, "error": "unknown_tool"}

        for round_index in range(self.max_tool_rounds):
            llm_started = utcnow()
            try:
                with anyio.fail_after(self.provider_timeout_seconds):
                    turn = await anyio.to_thread.run_sync(
                        lambda: self.llm.tool_turn(system, messages, TOOLS),
                        abandon_on_cancel=True,
                    )
            except (LLMTimeoutError, TimeoutError) as exc:
                failure = (
                    "The model provider took too long to respond. Please try "
                    "again or check the model provider connection."
                )
                self._llm_span(
                    recorder, round_index, llm_started, system, messages,
                    error=str(exc) or failure,
                )
                finish("failed", "timeout", failure)
                raise AssistantUnavailable(failure) from exc
            except LLMError as exc:
                self._llm_span(
                    recorder, round_index, llm_started, system, messages, error=str(exc)
                )
                finish("failed", "llm_error", None)
                raise AssistantUnavailable("The assistant LLM is unavailable") from exc
            tokens_in += turn.tokens_in
            tokens_out += turn.tokens_out
            self._llm_span(recorder, round_index, llm_started, system, messages, turn=turn)
            if not turn.tool_calls:
                if not turn.text.strip():
                    finish("failed", "empty_answer", None)
                    raise AssistantUnavailable("The assistant LLM returned no answer")
                finish("completed", "final_answer", turn.text)
                return {
                    "answer": turn.text,
                    "actions": actions,
                    "tool_trace": trace,
                    "provenance": provenance,
                    "request_id": request_id,
                    "trace_run_id": recorder.run.id if recorder else None,
                }
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.text,
                    "tool_calls": [
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                        for call in turn.tool_calls
                    ],
                }
            )
            for call in turn.tool_calls:
                tool_started = utcnow()
                if call.parse_error:
                    result: dict[str, Any] = {
                        "ok": False,
                        "error": "invalid_tool_arguments",
                        "message": call.parse_error,
                    }
                else:
                    try:
                        result = run_tool(call.name, dict(call.arguments))
                    except Exception as exc:  # noqa: BLE001 - report to the LLM, keep going
                        LOGGER.exception("assistant tool %s failed", call.name)
                        result = {"ok": False, "error": "tool_failed", "message": str(exc)}
                encoded = json.dumps(result, default=str)
                if len(encoded) > self.max_result_chars:
                    result = {"ok": False, "error": "tool_result_too_large"}
                    encoded = json.dumps(result)
                ok = not (isinstance(result, dict) and (
                    result.get("error") or result.get("ok") is False
                ))
                trace.append({"name": call.name, "arguments": call.arguments, "ok": ok})
                if recorder:
                    recorder.span(
                        component="assistant",
                        kind="tool",
                        name=call.name,
                        status="success" if ok else "error",
                        started_at=tool_started,
                        completed_at=utcnow(),
                        input={
                            "raw_arguments": call.raw_arguments,
                            "parsed_arguments": call.arguments,
                        },
                        output=result,
                        attributes={"round": round_index + 1, "tool_call_id": call.id},
                        error=None if ok else str(result.get("message") or result.get("error")),
                    )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": encoded}
                )
        finish("failed", "tool_round_limit", None)
        raise AssistantUnavailable("The assistant exceeded its tool-call limit")

    def _llm_span(
        self,
        recorder: TraceRecorder | None,
        round_index: int,
        started_at: Any,
        system: str,
        messages: list[dict[str, Any]],
        *,
        turn: Any = None,
        error: str | None = None,
    ) -> None:
        if recorder is None:
            return
        recorder.span(
            component="assistant",
            kind="llm",
            name=f"LLM round {round_index + 1}",
            status="error" if error else "success",
            started_at=started_at,
            completed_at=utcnow(),
            input={"system": system, "messages": messages, "tools": TOOLS},
            output=(
                {
                    "text": turn.text,
                    "tool_calls": [
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                        for call in turn.tool_calls
                    ],
                }
                if turn is not None
                else None
            ),
            error=error,
            attributes={"round": round_index + 1},
        )

    @staticmethod
    def _navigate(
        arguments: dict[str, Any],
        organization_id: str,
        model_id: str | None,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        resolved = resolve_route(str(arguments.get("route") or ""))
        if resolved is None:
            return {
                "ok": False,
                "error": "unknown_route",
                "routes": list(ROUTE_CATALOG),
            }
        route, query_params = resolved
        params = dict(query_params)
        supplied = arguments.get("params")
        if isinstance(supplied, dict):
            params.update({str(key): value for key, value in supplied.items()})
        params["organization"] = organization_id
        if model_id:
            params.setdefault("model", model_id)
        label = str(arguments.get("label") or "").strip() or _route_label(route)
        actions.append(
            {"type": "navigate", "route": route, "params": params, "label": label}
        )
        return {"ok": True, "route": route, "params": params}


# ---------------------------------------------------------------- endpoint

class AssistantHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class AssistantLocation(BaseModel):
    pathname: str = Field(default="", max_length=500)
    search: str = Field(default="", max_length=2_000)


class AssistantTurnRequest(BaseModel):
    organization: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    message: str = Field(max_length=2000)
    history: list[AssistantHistoryMessage] = Field(default_factory=list, max_length=100)
    location: AssistantLocation | None = None


def assistant_service(
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
) -> AssistantService:
    override = getattr(request.app.state, "assistant_service", None)
    if override is not None:
        return override
    try:
        session_id = audit.normalize_correlation_id(
            request.headers.get("x-helios-session-id")
        )
        settings = (
            _model_provider_store(request).get(principal.id, session_id)
            if session_id is not None
            else None
        )
        llm = llm_for_settings(settings) if settings is not None else llm_from_env()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    if llm is None:
        raise HTTPException(503, "No LLM provider is configured for the Helios assistant")
    return AssistantService(
        llm,
        trace_repository=getattr(request.app.state, "metadata_repository", None),
    )


@assistant_router.post("/turn")
async def assistant_turn(
    body: AssistantTurnRequest,
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
    service: Annotated[AssistantService, Depends(assistant_service)],
) -> dict[str, Any]:
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "message must not be empty")
    organization = _authorize_workspace(request, principal, policy, body.organization)
    history = [item.model_dump() for item in body.history[-MAX_HISTORY_MESSAGES:]]
    location = body.location.model_dump() if body.location else None
    try:
        return await service.turn(
            principal,
            organization.id,
            body.model,
            message,
            history,
            location,
            lambda: build_workspace_state(
                request, principal, policy, organization, body.model
            ),
        )
    except AssistantUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
