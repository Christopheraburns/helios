"""Background Talk-to-Your-Data model evaluation, in the console.

The evaluation itself is ``helios_serve.evaluation`` (burndown 6.5): suites, result
normalization and the aggregate metrics come from there. What stays here is the
console's way of running one: through its MCP server and conversation service, as a
console Principal, recording traces and results in the console's repository. It goes
with the console in Phase 8; ``helios eval`` and ``run_evaluation`` call the tools directly.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from helios_serve import evaluation as library

from apps.helios.console.conversation import (
    ConversationService,
    ConversationUnavailable,
    MCPClientConfig,
)
from helios_cortex import Caller, IdentityMode
from apps.helios.console.authz import Principal
from helios_cloudera.impala import impala_config
from helios_cloudera.workbench import WorkbenchSettings as Settings
from helios_cloudera.impala import ImpalaEngine
from helios_cortex.llm import LLMClient
from apps.helios.console.metadata import (
    EvaluationResult,
    EvaluationRun,
    MetadataRepository,
)

BUILTIN_SUITE = Path(str(library.BUILTIN_SUITES / "tpcds-v1.json"))


def _suite_directory() -> Path:
    return library.suite_directory(Settings())


def _suite_paths() -> list[Path]:
    return [BUILTIN_SUITE, *sorted(_suite_directory().glob("*.json"))]


def list_suites() -> list[dict[str, Any]]:
    return library.list_suites(_suite_directory())


def load_suite(
    suite_id: str = "tpcds",
    suite_version: str | None = None,
) -> dict[str, Any]:
    try:
        return library.load_suite(suite_id, suite_version, directory=_suite_directory())
    except LookupError:
        raise LookupError("evaluation suite not found") from None


def save_suite(document: dict[str, Any]) -> dict[str, Any]:
    # Saved where list_suites reads (the settings' data directory); this used to read
    # HELIOS_ROOT / CDSW_PROJECT_DIR itself.
    return library.save_suite(document, _suite_directory())


_validate_suite = library.validate_suite


async def execute_evaluation(
    repository: MetadataRepository,
    run: EvaluationRun,
    principal: Principal,
    baseline_llm: LLMClient,
    candidate_llm: LLMClient,
) -> None:
    started_at = datetime.now(timezone.utc)
    repository.update_evaluation_run(
        run.id,
        status="running",
        started_at=started_at,
    )
    try:
        suite = load_suite(run.suite_id, run.suite_version)
        configuration = impala_config(Settings())
        if configuration is None:
            raise RuntimeError("Impala is not configured for reference queries")
        if configuration.identity is not IdentityMode.IMPERSONATE:
            raise PermissionError("Impala proxy-user delegation is disabled")
        engine = ImpalaEngine(configuration)
        mcp = MCPClientConfig.from_env()
        variants = (
            ("baseline", baseline_llm),
            ("candidate", candidate_llm),
        )
        for question in suite["questions"]:
            if _cancelled(repository, run.id):
                repository.update_evaluation_run(
                    run.id,
                    status="cancelled",
                    completed_at=datetime.now(timezone.utc),
                )
                return
            reference = await anyio.to_thread.run_sync(
                lambda q=question: engine.query(
                    q["reference_sql"],
                    Caller(principal.subject),
                )
            )
            expected = _normalized_result(reference.columns, reference.rows)
            for variant, llm in variants:
                service = ConversationService(
                    llm,
                    mcp,
                    max_tool_rounds=run.max_tool_rounds,
                    trace_repository=repository,
                )
                for repetition in range(1, run.repetitions + 1):
                    if _cancelled(repository, run.id):
                        repository.update_evaluation_run(
                            run.id,
                            status="cancelled",
                            completed_at=datetime.now(timezone.utc),
                        )
                        return
                    trace_id = str(uuid.uuid4())
                    answer = None
                    completed = False
                    try:
                        answer = await service.turn(
                            principal,
                            run.organization_id,
                            run.model_id,
                            question["question"],
                            purpose="evaluation",
                            question_id=question["id"],
                            trace_run_id=trace_id,
                        )
                        completed = True
                    except ConversationUnavailable:
                        pass
                    actual = (
                        _normalized_query_result(answer.get("query_result"))
                        if answer
                        else None
                    )
                    metrics = _path_metrics(
                        repository,
                        trace_id,
                        question,
                    )
                    accurate = actual == expected
                    metrics.update({
                        "accurate": accurate,
                        "completed": completed,
                        "expected_result": expected,
                        "actual_result": actual,
                    })
                    repository.append_evaluation_result(
                        EvaluationResult(
                            id=str(uuid.uuid4()),
                            evaluation_run_id=run.id,
                            question_id=question["id"],
                            variant=variant,
                            repetition=repetition,
                            trace_run_id=trace_id,
                            accurate=accurate,
                            completed=completed,
                            metrics=metrics,
                        )
                    )
        results = repository.evaluation_results(run.id)
        repository.update_evaluation_run(
            run.id,
            status="completed",
            completed_at=datetime.now(timezone.utc),
            metrics=_aggregate_metrics(results, repository),
        )
    except Exception as exc:
        repository.update_evaluation_run(
            run.id,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            error=str(exc)[:2000],
        )


def _cancelled(repository: MetadataRepository, run_id: str) -> bool:
    current = repository.evaluation_run(run_id)
    return bool(current and current.cancel_requested)


_normalized_query_result = library.normalized_query_result
_normalized_result = library.normalized_result


def _path_metrics(
    repository: MetadataRepository,
    trace_id: str,
    question: dict[str, Any],
) -> dict[str, Any]:
    trace = repository.trace_run(trace_id)
    spans = [
        span
        for span in repository.trace_spans(trace_id)
        if span.component == "agent" and span.kind == "tool"
    ]
    keys: list[str] = []
    errors: list[bool] = []
    invalid = 0
    grounded = 0
    grounding = [str(item).casefold() for item in question.get("grounding", [])]
    for span in spans:
        payload = span.input if isinstance(span.input, dict) else {}
        arguments = payload.get("parsed_arguments") or {}
        key = json.dumps(
            {"tool": span.name, "arguments": arguments},
            sort_keys=True,
            default=str,
        )
        keys.append(key)
        is_error = span.status == "error"
        errors.append(is_error)
        output = span.output if isinstance(span.output, dict) else {}
        if output.get("error") in {
            "invalid_tool_arguments",
            "unknown_tool",
        }:
            invalid += 1
        argument_text = json.dumps(arguments, default=str).casefold()
        if not grounding or any(item in argument_text for item in grounding):
            grounded += 1
    redundant = len(keys) - len(set(keys))
    recovery_attempts = sum(errors[:-1])
    recovered = sum(
        errors[index] and keys[index + 1] != keys[index]
        for index in range(max(len(keys) - 1, 0))
    )
    minimum = len(question.get("minimum_tools") or [])
    return {
        "tool_calls": len(spans),
        "excess_tool_calls": max(len(spans) - minimum, 0),
        "invalid_calls": invalid,
        "redundant_calls": redundant,
        "recovery_attempts": recovery_attempts,
        "recovered": recovered,
        "grounded_calls": grounded,
        "tokens_in": trace.tokens_in if trace else 0,
        "tokens_out": trace.tokens_out if trace else 0,
        "duration_ms": trace.duration_ms if trace else None,
    }


def _aggregate_metrics(
    results: list[EvaluationResult],
    repository: MetadataRepository | None,
) -> dict[str, Any]:
    return library.aggregate(results)
