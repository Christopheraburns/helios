"""Onboarding journeys as data, evaluated deterministically against workspace state.

The LLM narrates; these predicates decide what is done.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

State = dict[str, Any]
Predicate = Callable[[State], bool]


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    description: str
    route: str
    done: Predicate
    params: dict[str, Any] = field(default_factory=dict)
    doc_slug: str | None = None
    external_note: str | None = None
    external_when: Predicate | None = None
    params_for: Callable[[State], dict[str, Any]] | None = None

    def status(self, state: State) -> str:
        if self.done(state):
            return "done"
        if self.external_note and (
            self.external_when is None or self.external_when(state)
        ):
            return "external"
        return "todo"

    def evaluate(self, state: State) -> dict[str, Any]:
        status = self.status(state)
        params = dict(self.params)
        if self.params_for is not None:
            params.update(self.params_for(state))
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "route": self.route,
            "params": params,
            "doc_slug": self.doc_slug,
            "status": status,
            "note": self.external_note if status == "external" else None,
        }


@dataclass(frozen=True)
class Journey:
    id: str
    title: str
    description: str
    steps: tuple[Step, ...]

    def evaluate(self, state: State) -> dict[str, Any]:
        steps = [step.evaluate(state) for step in self.steps]
        done = sum(step["status"] == "done" for step in steps)
        next_step = next(
            (step["id"] for step in steps if step["status"] != "done"), None
        )
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "progress": {"done": done, "total": len(steps)},
            "next_step_id": next_step,
            "steps": steps,
        }


def evaluate(journeys: tuple[Journey, ...], state: State) -> list[dict[str, Any]]:
    return [journey.evaluate(state) for journey in journeys]


def selected_model(state: State) -> dict[str, Any] | None:
    models = state.get("models") or []
    selected = state.get("selected_model_id")
    for model in models:
        if model.get("id") == selected:
            return model
    return models[0] if models else None


def _lifecycle(state: State, key: str) -> Any:
    model = selected_model(state)
    if model is None:
        return None
    return (model.get("lifecycle") or {}).get(key)


def _llm_configured(state: State) -> bool:
    return bool((state.get("llm_provider") or {}).get("configured"))


def _has_data_source(state: State) -> bool:
    return bool(state.get("data_sources"))


def _has_crawl_source(state: State) -> bool:
    return any(
        source.get("crawl_enabled") for source in state.get("data_sources") or []
    )


def _has_model(state: State) -> bool:
    return bool(state.get("models"))


def _proposals_ready(state: State) -> bool:
    return _lifecycle(state, "discovery_status") in {"proposals_ready"}


def _review_complete(state: State) -> bool:
    return _lifecycle(state, "review_status") == "complete"


def _review_params(state: State) -> dict[str, Any]:
    run_id = _lifecycle(state, "latest_run_id")
    return {"review_run_id": run_id} if run_id else {}


def _published(state: State) -> bool:
    return _lifecycle(state, "publication_state") == "published"


def _has_conversations(state: State) -> bool:
    model = selected_model(state)
    return bool(model and model.get("has_conversations"))


def _ontology_published(state: State) -> bool:
    return int((state.get("ontology") or {}).get("version_count") or 0) > 0


def _ontology_active(state: State) -> bool:
    return (state.get("ontology") or {}).get("active_version") is not None


def _crawl_run_exists(state: State) -> bool:
    return int((state.get("crawl_runs") or {}).get("total") or 0) > 0


def _last_crawl_succeeded(state: State) -> bool:
    last = (state.get("crawl_runs") or {}).get("last") or {}
    return last.get("status") == "SUCCEEDED"


CONFIGURE_LLM = Step(
    id="configure-llm",
    title="Connect an LLM provider",
    description=(
        "Helios uses a language model to propose semantics and to answer "
        "questions. Pick a provider and enter a key for this browser session, "
        "or configure one in the deployment environment."
    ),
    route="/governance/model-provider",
    done=_llm_configured,
    doc_slug="walkthrough",
)

SEMANTIC_MODEL_JOURNEY = Journey(
    id="semantic-model",
    title="Create and publish a semantic model",
    description=(
        "Go from warehouse tables to a published semantic model that people "
        "and agents can query: harvest, profile, propose, review, publish, ask."
    ),
    steps=(
        CONFIGURE_LLM,
        Step(
            id="add-data-source",
            title="Add a data source",
            description=(
                "Register the warehouse or lakehouse connection whose tables "
                "the model will describe."
            ),
            route="/data-sources",
            done=_has_data_source,
            doc_slug="walkthrough",
        ),
        Step(
            id="create-model",
            title="Create a semantic model",
            description=(
                "A model names the data source and the tables it covers; "
                "everything else is discovered."
            ),
            route="/model-overview",
            done=_has_model,
            doc_slug="walkthrough",
            external_note=(
                "Models are created outside the UI today: register one with "
                "the Helios metadata CLI or the Workbench model-setup job, "
                "then refresh."
            ),
        ),
        Step(
            id="run-discovery",
            title="Run discovery (harvest, profile, propose)",
            description=(
                "Discovery reads the tables, measures the data, and proposes "
                "datasets, relationships, metrics and glossary terms."
            ),
            route="/models",
            done=_proposals_ready,
            doc_slug="console-runs",
            external_note=(
                "Discovery runs as a Workbench Job; the UI cannot launch it yet."
            ),
        ),
        Step(
            id="review-proposals",
            title="Review proposals",
            description=(
                "Accept, edit or reject each proposal on the canvas in review "
                "mode until nothing is pending."
            ),
            route="/canvas",
            done=_review_complete,
            doc_slug="console-review",
            params_for=_review_params,
        ),
        Step(
            id="publish-model",
            title="Publish the model",
            description=(
                "Publishing writes the approved semantics as an Ossie file "
                "that Talk to Your Data and MCP agents use."
            ),
            route="/model-overview",
            done=_published,
            doc_slug="console-review",
        ),
        Step(
            id="ask-question",
            title="Ask a question in Talk to Your Data",
            description=(
                "Ask a plain-language question of the published model and "
                "see the compiled query and its result."
            ),
            route="/talk",
            done=_has_conversations,
            doc_slug="mcp-server",
        ),
    ),
)

UNSTRUCTURED_CRAWL_JOURNEY = Journey(
    id="unstructured-crawl",
    title="Crawl unstructured data with an ontology",
    description=(
        "Publish and activate an ontology, point a crawl-enabled data source "
        "at your documents, and run the crawler to classify them."
    ),
    steps=(
        CONFIGURE_LLM,
        Step(
            id="publish-ontology",
            title="Publish an ontology version",
            description=(
                "Check and publish an ontology schema so the crawler knows "
                "which classes and claims to look for."
            ),
            route="/ontology",
            done=_ontology_published,
            doc_slug="ontology-deployment",
        ),
        Step(
            id="activate-ontology",
            title="Activate an ontology version",
            description=(
                "Exactly one published version is active; the crawler and "
                "the canvas ontology lens use it."
            ),
            route="/ontology",
            done=_ontology_active,
            doc_slug="ontology-deployment",
        ),
        Step(
            id="add-crawl-source",
            title="Add a crawl-enabled data source",
            description=(
                "Add an object-store or document source with crawling "
                "enabled and a scope the crawler can read."
            ),
            route="/data-sources",
            done=_has_crawl_source,
            doc_slug="ontology-deployment",
        ),
        Step(
            id="run-crawl",
            title="Run the crawler",
            description=(
                "The crawler reads each asset, classifies it against the "
                "active ontology and records claims in the index."
            ),
            route="/crawler",
            done=_crawl_run_exists,
            doc_slug="crawler-guide",
        ),
        Step(
            id="review-crawl",
            title="Review crawl results",
            description=(
                "Open the latest crawl run to see assets by status and class, "
                "and fix anything that failed."
            ),
            route="/crawler",
            done=_last_crawl_succeeded,
            doc_slug="crawler-guide",
        ),
    ),
)

JOURNEYS: tuple[Journey, ...] = (
    SEMANTIC_MODEL_JOURNEY,
    UNSTRUCTURED_CRAWL_JOURNEY,
)
