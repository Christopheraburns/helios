"""Background Talk-to-Your-Data model evaluation."""
from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio

from apps.helios.console.conversation import (
    ConversationService,
    ConversationUnavailable,
    MCPClientConfig,
)
from helios_core.authz import Principal
from helios_core.config import impala_config
from helios_core.engines import ImpalaEngine
from helios_core.llm import LLMClient
from helios_core.metadata import (
    EvaluationResult,
    EvaluationRun,
    MetadataRepository,
)

HERE = Path(__file__).resolve().parents[2]
BUILTIN_SUITE = HERE / "eval" / "tpcds-v1.json"


def _suite_paths() -> list[Path]:
    root = Path(
        os.environ.get("HELIOS_ROOT")
        or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
    )
    return [
        BUILTIN_SUITE,
        *sorted((root / "state" / "eval-suites").glob("*.json")),
    ]


def list_suites() -> list[dict[str, Any]]:
    suites: dict[tuple[str, str], dict[str, Any]] = {}
    for path in _suite_paths():
        document = json.loads(path.read_text())
        _validate_suite(document)
        suites.setdefault((document["id"], document["version"]), document)
    return sorted(
        suites.values(),
        key=lambda item: (item["id"], item["version"]),
    )


def load_suite(
    suite_id: str = "tpcds",
    suite_version: str | None = None,
) -> dict[str, Any]:
    matches = [
        document
        for document in list_suites()
        if document["id"] == suite_id
        and (suite_version is None or document["version"] == suite_version)
    ]
    if matches:
        return matches[-1]
    raise LookupError("evaluation suite not found")


def save_suite(document: dict[str, Any]) -> dict[str, Any]:
    _validate_suite(document)
    root = Path(
        os.environ.get("HELIOS_ROOT")
        or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
    )
    directory = root / "state" / "eval-suites"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{document['id']}-{document['version']}.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def _validate_suite(document: dict[str, Any]) -> None:
    if not isinstance(document.get("id"), str) or not document["id"]:
        raise ValueError("evaluation suite requires an id")
    if not isinstance(document.get("version"), str) or not document["version"]:
        raise ValueError("evaluation suite requires a version")
    questions = document.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 100:
        raise ValueError("evaluation suite must contain 1 to 100 questions")
    identifiers: set[str] = set()
    for item in questions:
        if not isinstance(item, dict):
            raise ValueError("evaluation questions must be objects")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("each evaluation question requires an id")
        if identifier in identifiers:
            raise ValueError("evaluation question ids must be unique")
        identifiers.add(identifier)
        for field in ("question", "reference_sql"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"evaluation question requires {field}")
        for field in ("minimum_tools", "grounding"):
            values = item.get(field)
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(value, str) and value for value in values)
            ):
                raise ValueError(
                    f"evaluation question requires non-empty {field}"
                )


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
        configuration = impala_config()
        if configuration is None:
            raise RuntimeError("Impala is not configured for reference queries")
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
                    delegated_user=principal.subject,
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


def _normalized_query_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    return _normalized_result(result.get("columns") or [], result.get("rows") or [])


def _normalized_result(columns: Any, rows: Any) -> dict[str, Any]:
    normalized_rows = [
        [None if value is None else str(value) for value in row]
        for row in rows
    ]
    normalized_rows.sort(key=lambda row: json.dumps(row, sort_keys=True))
    return {
        "columns": [str(column).casefold() for column in columns],
        "rows": normalized_rows,
    }


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
    repository: MetadataRepository,
) -> dict[str, Any]:
    grouped: dict[str, list[EvaluationResult]] = defaultdict(list)
    for result in results:
        grouped[result.variant].append(result)
    response = {}
    for variant, items in grouped.items():
        metrics = [item.metrics or {} for item in items]
        correct = sum(item.accurate for item in items)
        completed = sum(item.completed for item in items)
        tool_calls = sum(item.get("tool_calls", 0) for item in metrics)
        invalid_calls = sum(item.get("invalid_calls", 0) for item in metrics)
        redundant_calls = sum(
            item.get("redundant_calls", 0) for item in metrics
        )
        response[variant] = {
            "runs": len(items),
            "answer_accuracy": correct / len(items) if items else 0,
            "completion_rate": completed / len(items) if items else 0,
            "tool_calls": tool_calls,
            "excess_tool_calls": sum(
                item.get("excess_tool_calls", 0) for item in metrics
            ),
            "invalid_calls": invalid_calls,
            "invalid_call_rate": invalid_calls / max(tool_calls, 1),
            "redundant_calls": redundant_calls,
            "redundant_call_rate": redundant_calls / max(tool_calls, 1),
            "recovery_rate": (
                sum(item.get("recovered", 0) for item in metrics)
                / max(sum(item.get("recovery_attempts", 0) for item in metrics), 1)
            ),
            "grounding_rate": (
                sum(item.get("grounded_calls", 0) for item in metrics)
                / max(sum(item.get("tool_calls", 0) for item in metrics), 1)
            ),
            "tokens_per_correct_answer": (
                sum(
                    item.get("tokens_in", 0) + item.get("tokens_out", 0)
                    for item in metrics
                ) / max(correct, 1)
            ),
            "milliseconds_per_correct_answer": (
                sum(item.get("duration_ms") or 0 for item in metrics)
                / max(correct, 1)
            ),
        }
    return response
