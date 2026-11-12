"""Evaluate how well an LLM answers questions through the Helios tools.

A suite is a JSON document of questions, each with the SQL whose result is the right
answer (``reference_sql``), the fewest tools a good answer needs (``minimum_tools``) and
names a grounded answer mentions in its tool arguments (``grounding``)::

    {"id": "tpcds", "version": "1.0.0", "model_id": "tpcds",
     "questions": [{"id": "Q01", "question": "What is total store sales revenue?",
                    "reference_sql": "SELECT SUM(ss_net_paid) AS ... FROM tpcds.store_sales",
                    "minimum_tools": ["search_semantics", "run_query"],
                    "grounding": ["store_sales_revenue"]}]}

``run_evaluation`` asks each question of each LLM variant (a baseline and a candidate,
say) through ``answer_question``: an agent loop that offers the LLM the registry's tools
and calls the tool functions directly, as the caller, with no MCP server in between.
An answer is accurate when the last query it ran returns the reference result, compared
without regard to row order, column-name case or value types. Path metrics (tool calls,
excess, invalid and redundant calls, recovery, grounding, tokens, time) say how it got
there; ``aggregate`` sums them per variant.

The built-in suite (``tpcds`` 1.0.0, for the TPC-DS sample model) ships in the package;
a deployment's own suites live in ``<data_dir>/state/eval-suites``. ``helios eval`` is
the command line.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from helios_serve.registry import Registry, load_registry
from helios_serve.stores import Stores, model_connection
from helios_serve.tools import MAX_ROWS

if TYPE_CHECKING:
    from helios_cortex.caller import Caller
    from helios_cortex.llm import LLMClient
    from helios_cortex.settings import Settings

LOGGER = logging.getLogger(__name__)

MAX_QUESTIONS = 100
SEMANTIC_ERRORS = frozenset({"invalid_filter_value_type", "invalid_semantic_query", "invalid_semantic_reference",
                             "semantic_not_found"})
INVALID_CALLS = frozenset({"invalid_tool_arguments", "unknown_tool"})
RECOVERY = ("Call search_semantics again. Copy metric names exactly and use full database.table.column field "
            "identifiers from the tool results before retrying. Preserve each field's returned datatype; numeric "
            "filter values must be JSON numbers without quotes.")
SYSTEM_PROMPT = (
    "You are a data assistant. Answer only from the supplied Helios tools, which describe a semantic model of "
    "the data warehouse and run queries on it.\n\n"
    "Before compiling or running a query, call search_semantics and copy metric names exactly from its results. "
    "Dimensions and filter columns must use exact database.table.column identifiers returned by search_semantics "
    "or describe. Never invent, shorten or convert semantic names. Preserve datatypes: JSON numbers for numeric "
    "fields, booleans for Boolean ones, ISO strings for dates and timestamps. If a tool reports an invalid query "
    "or filter value, search again and correct the identifiers and value types.\n\n"
    "Explain results accurately and do not invent data. Semantic validation, authorization and argument errors "
    "are not warehouse outages. If a tool reports that it is unavailable, say so plainly.")


class EvaluationError(RuntimeError):
    """The evaluation cannot run: no such suite, an invalid suite, or a reference query
    that fails."""


# ---------------------------------------------------------------------------- suites
BUILTIN_SUITES = files("helios_serve") / "suites"


def suite_directory(settings: Settings) -> Path:
    """Where a deployment's own suites are kept."""
    return Path(settings.data_dir) / "state" / "eval-suites"


def validate_suite(document: Any) -> None:
    """Raise ValueError unless ``document`` is a suite: an id, a version and 1 to 100
    questions with unique ids, each with question, reference_sql, minimum_tools and grounding."""
    if not isinstance(document, dict):
        raise ValueError("an evaluation suite is a JSON object")
    for key in ("id", "version"):
        if not isinstance(document.get(key), str) or not document[key]:
            raise ValueError(f"evaluation suite requires {'an id' if key == 'id' else 'a version'}")
    questions = document.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError(f"evaluation suite must contain 1 to {MAX_QUESTIONS} questions")
    seen: set[str] = set()
    for item in questions:
        if not isinstance(item, dict):
            raise ValueError("evaluation questions must be objects")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("each evaluation question requires an id")
        if identifier in seen:
            raise ValueError("evaluation question ids must be unique")
        seen.add(identifier)
        for key in ("question", "reference_sql"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"evaluation question requires {key}")
        for key in ("minimum_tools", "grounding"):
            values = item.get(key)
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                raise ValueError(f"evaluation question requires non-empty {key}")


def read_suite(path: Path | str) -> dict[str, Any]:
    """The suite in a JSON file, validated."""
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read the suite {path}: {exc}") from exc
    try:
        validate_suite(document)
    except ValueError as exc:
        raise EvaluationError(f"{path}: {exc}") from exc
    return document


def _suite_documents(directory: Path | None) -> list[dict[str, Any]]:
    texts = [p.read_text() for p in sorted(BUILTIN_SUITES.iterdir(), key=lambda p: p.name) if p.name.endswith(".json")]
    if directory is not None and Path(directory).is_dir():
        texts += [p.read_text() for p in sorted(Path(directory).glob("*.json"))]
    documents = []
    for text in texts:
        document = json.loads(text)
        validate_suite(document)
        documents.append(document)
    return documents


def list_suites(directory: Path | None = None) -> list[dict[str, Any]]:
    """The built-in suites and those in ``directory``, by id and version; a built-in one
    wins over a saved one with the same id and version."""
    suites: dict[tuple[str, str], dict[str, Any]] = {}
    for document in _suite_documents(directory):
        suites.setdefault((document["id"], document["version"]), document)
    return sorted(suites.values(), key=lambda d: (d["id"], d["version"]))


def load_suite(suite_id: str = "tpcds", version: str | None = None, *,
               directory: Path | None = None) -> dict[str, Any]:
    """The suite ``suite_id`` at ``version``, or its last version; LookupError when there is none."""
    matches = [d for d in list_suites(directory)
               if d["id"] == suite_id and (version is None or d["version"] == version)]
    if not matches:
        raise LookupError(f"evaluation suite {suite_id!r}" + (f" version {version!r}" if version else "")
                          + " not found")
    return matches[-1]


def save_suite(document: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Validate ``document`` and keep it in ``directory`` as ``<id>-<version>.json``."""
    validate_suite(document)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{document['id']}-{document['version']}.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


# ---------------------------------------------------------------------------- results
def normalized_result(columns: Any, rows: Any) -> dict[str, Any]:
    """A query result in a form two equal answers share: column names case-folded,
    values as strings (None kept), rows in a fixed order."""
    normalized = [[None if v is None else str(v) for v in row] for row in rows]
    normalized.sort(key=lambda row: json.dumps(row, sort_keys=True))
    return {"columns": [str(c).casefold() for c in columns], "rows": normalized}


def normalized_query_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    return normalized_result(result.get("columns") or [], result.get("rows") or [])


# ---------------------------------------------------------------------------- answering
@dataclass
class ToolCallRecord:
    """One tool call the LLM made, with the arguments as called (``model`` pinned)."""

    tool: str
    arguments: dict[str, Any]
    result: Any
    duration_ms: float = 0.0

    @property
    def error(self) -> str | None:
        return self.result.get("error") if isinstance(self.result, dict) else None


@dataclass
class Answer:
    """How an LLM answered one question. ``termination`` is ``final_answer``, or why it
    stopped: ``tool_error`` (a tool error it did not recover from), ``tool_round_limit``,
    ``empty_answer`` or ``llm_error``."""

    text: str | None
    termination: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: float = 0.0
    error: str | None = None

    @property
    def completed(self) -> bool:
        return self.termination == "final_answer"

    @property
    def query_result(self) -> dict[str, Any] | None:
        """The last ``run_query`` result that returned rows."""
        for call in reversed(self.tool_calls):
            if call.tool == "run_query" and isinstance(call.result, dict) and "rows" in call.result:
                return call.result
        return None


def _llm_errors() -> tuple[type[BaseException], ...]:
    """LLMError when helios-cortex's LLM client is installed (the ``eval`` extra); any
    object with ``tool_turn`` will do as the LLM, so the loop does not need it."""
    try:
        from helios_cortex.llm import LLMError
    except ModuleNotFoundError:
        return ()
    return (LLMError,)


def tool_definitions(registry: Registry) -> list[dict[str, Any]]:
    """The registry's tools as ``LLMClient.tool_turn`` takes them."""
    return [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in registry.tools()]


def answer_question(llm: LLMClient, question: str, *, stores: Stores, caller: Caller | None = None,
                    model: str | None = None, registry: Registry | None = None, max_tool_rounds: int = 6,
                    max_result_chars: int = 50_000, system: str = SYSTEM_PROMPT) -> Answer:
    """Let ``llm`` (an ``LLMClient``, or anything with its ``tool_turn``) answer
    ``question`` with the registry's tools, called directly as
    ``caller``. With ``model``, every tool that takes a model uses it. The loop stops at a
    final answer, after ``max_tool_rounds`` rounds, after two semantic errors in a row, or
    when the LLM repeats a call that failed and cannot succeed."""
    registry = registry if registry is not None else load_registry()
    tools = tool_definitions(registry)
    if model is not None:
        system += f"\n\nAnswer from the model {model!r}."
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    answer = Answer(None, "tool_round_limit")
    started = time.perf_counter()
    unresolved: dict[str, Any] | None = None
    semantic_failures = 0
    failed_calls: set[str] = set()

    def done(termination: str, text: str | None = None, error: str | None = None) -> Answer:
        answer.termination, answer.text, answer.error = termination, text, error
        answer.duration_ms = (time.perf_counter() - started) * 1000
        return answer

    for _ in range(max_tool_rounds):
        try:
            turn = llm.tool_turn(system, messages, tools)
        except _llm_errors() as exc:
            return done("llm_error", error=str(exc))
        answer.tokens_in += turn.tokens_in
        answer.tokens_out += turn.tokens_out
        if not turn.tool_calls:
            if unresolved is not None:
                return done("tool_error", turn.text or None, error=str(unresolved.get("error")))
            if not turn.text.strip():
                return done("empty_answer")
            return done("final_answer", turn.text)
        messages.append({"role": "assistant", "content": turn.text,
                         "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                        for c in turn.tool_calls]})
        for call in turn.tool_calls:
            arguments = dict(call.arguments)
            tool = registry.get(call.name)
            call_started = time.perf_counter()
            if call.parse_error:
                result: Any = {"error": "invalid_tool_arguments", "message": call.parse_error, "retryable": False}
            elif tool is None:
                result = {"error": "unknown_tool", "message": f"there is no tool named {call.name!r}",
                          "retryable": False}
            else:
                if model is not None and "model" in tool.input_schema["properties"]:
                    arguments["model"] = model
                try:
                    result = tool(caller, stores, **arguments)
                except TypeError as exc:                     # arguments the function does not take
                    result = {"error": "invalid_tool_arguments", "message": str(exc), "retryable": False}
                except Exception as exc:
                    LOGGER.warning("tool %s raised %s", call.name, type(exc).__name__)
                    result = {"error": "tool_failed", "message": "the tool failed", "retryable": True}
            code = result.get("error") if isinstance(result, dict) else None
            if code in SEMANTIC_ERRORS:
                semantic_failures += 1
                result = {**result, "recovery": RECOVERY}
            if code:
                unresolved = result
            elif call.name in ("compile_query", "run_query"):
                unresolved, semantic_failures = None, 0
            encoded = json.dumps(result, default=str)
            if len(encoded) > max_result_chars:
                result = {"error": "tool_result_too_large", "message": "the tool result exceeded the safe limit",
                          "retryable": False}
                encoded = json.dumps(result)
            answer.tool_calls.append(ToolCallRecord(call.name, arguments, result,
                                                    (time.perf_counter() - call_started) * 1000))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": encoded})
            repeated = False
            if code and not (isinstance(result, dict) and result.get("retryable") is True):
                key = json.dumps({"tool": call.name, "arguments": arguments, "error": code}, sort_keys=True,
                                 default=str)
                repeated = key in failed_calls
                failed_calls.add(key)
            if (code in SEMANTIC_ERRORS and semantic_failures >= 2) or repeated:
                return done("tool_error", error=code)
    if unresolved is not None:
        return done("tool_error", error=str(unresolved.get("error")))
    return done("tool_round_limit")


def path_metrics(answer: Answer, question: Mapping[str, Any]) -> dict[str, Any]:
    """How ``answer`` got there: tool calls (and how many beyond ``minimum_tools``),
    invalid calls, redundant (repeated) calls, recovery from errors, calls whose arguments
    mention a ``grounding`` name, tokens and time."""
    keys = [json.dumps({"tool": c.tool, "arguments": c.arguments}, sort_keys=True, default=str)
            for c in answer.tool_calls]
    errors = [c.error is not None for c in answer.tool_calls]
    grounding = [str(g).casefold() for g in question.get("grounding") or []]
    grounded = sum(not grounding or any(g in json.dumps(c.arguments, default=str).casefold() for g in grounding)
                   for c in answer.tool_calls)
    return {
        "tool_calls": len(keys),
        "excess_tool_calls": max(len(keys) - len(question.get("minimum_tools") or []), 0),
        "invalid_calls": sum(c.error in INVALID_CALLS for c in answer.tool_calls),
        "redundant_calls": len(keys) - len(set(keys)),
        "recovery_attempts": sum(errors[:-1]),
        "recovered": sum(errors[i] and keys[i + 1] != keys[i] for i in range(max(len(keys) - 1, 0))),
        "grounded_calls": grounded,
        "tokens_in": answer.tokens_in,
        "tokens_out": answer.tokens_out,
        "duration_ms": answer.duration_ms,
    }


def aggregate(results: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Per variant: runs, answer accuracy, completion rate, call counts and rates, recovery
    and grounding rates, and tokens and milliseconds per correct answer. A result is anything
    with ``variant``, ``accurate``, ``completed`` and ``metrics`` (a ``QuestionResult``)."""
    grouped: dict[str, list[Any]] = defaultdict(list)
    for result in results:
        grouped[result.variant].append(result)
    report = {}
    for variant, items in grouped.items():
        metrics = [item.metrics or {} for item in items]

        def total(key: str) -> float:
            return sum(m.get(key) or 0 for m in metrics)

        correct = sum(bool(item.accurate) for item in items)
        tool_calls = total("tool_calls")
        report[variant] = {
            "runs": len(items),
            "answer_accuracy": correct / len(items) if items else 0,
            "completion_rate": sum(bool(item.completed) for item in items) / len(items) if items else 0,
            "tool_calls": tool_calls,
            "excess_tool_calls": total("excess_tool_calls"),
            "invalid_calls": total("invalid_calls"),
            "invalid_call_rate": total("invalid_calls") / max(tool_calls, 1),
            "redundant_calls": total("redundant_calls"),
            "redundant_call_rate": total("redundant_calls") / max(tool_calls, 1),
            "recovery_rate": total("recovered") / max(total("recovery_attempts"), 1),
            "grounding_rate": total("grounded_calls") / max(tool_calls, 1),
            "tokens_per_correct_answer": (total("tokens_in") + total("tokens_out")) / max(correct, 1),
            "milliseconds_per_correct_answer": total("duration_ms") / max(correct, 1),
        }
    return report


# ---------------------------------------------------------------------------- running a suite
@dataclass
class QuestionResult:
    question_id: str
    variant: str
    repetition: int
    accurate: bool
    completed: bool
    metrics: dict[str, Any]
    answer: Answer

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationReport:
    suite_id: str
    suite_version: str
    model: str | None
    results: list[QuestionResult]
    metrics: dict[str, dict[str, Any]]
    cancelled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def reference_result(sql: str, *, stores: Stores, caller: Caller | None = None,
                     model: str | None = None) -> dict[str, Any]:
    """The normalized result of a question's reference SQL, run as ``caller`` on the
    model's connection (the default connection without a registered model)."""
    registered = stores.registry.model(model) if model else None
    if registered is not None:
        connection, conflict = model_connection(stores.settings, stores.registry, registered)
        if conflict:
            raise EvaluationError(conflict)
    else:
        connection = stores.settings.resolve_connection()
    engine = stores.engines(connection)
    if engine is None:
        raise EvaluationError("no warehouse connection is configured for the reference queries")
    try:
        result = engine.query(sql, caller, limit=MAX_ROWS)
    except Exception as exc:
        raise EvaluationError(f"a reference query failed: {type(exc).__name__}: {exc}") from exc
    return normalized_result(result.columns, result.rows)


def run_evaluation(suite: Mapping[str, Any], stores: Stores, variants: Mapping[str, LLMClient], *,
                   caller: Caller | None = None, model: str | None = None, repetitions: int = 1,
                   max_tool_rounds: int = 6, registry: Registry | None = None,
                   on_result: Callable[[QuestionResult], None] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> EvaluationReport:
    """Ask every question of ``suite`` of each variant (``{"baseline": llm, ...}``)
    ``repetitions`` times, on ``model`` (default: the suite's ``model_id``), as ``caller``.
    ``on_result`` sees each result as it is scored; ``cancelled`` is asked before each
    answer, and a True stops the run with what is done so far. Raises EvaluationError when
    the suite is invalid or a reference query fails."""
    try:
        validate_suite(dict(suite))
    except ValueError as exc:
        raise EvaluationError(str(exc)) from exc
    if not variants:
        raise EvaluationError("an evaluation needs at least one LLM variant")
    if repetitions < 1:
        raise EvaluationError("repetitions must be at least 1")
    model = model if model is not None else suite.get("model_id")
    registry = registry if registry is not None else load_registry()
    results: list[QuestionResult] = []
    stopped = False
    for question in suite["questions"]:
        expected = reference_result(question["reference_sql"], stores=stores, caller=caller, model=model)
        for variant, llm in variants.items():
            for repetition in range(1, repetitions + 1):
                if cancelled is not None and cancelled():
                    stopped = True
                    break
                answer = answer_question(llm, question["question"], stores=stores, caller=caller, model=model,
                                         registry=registry, max_tool_rounds=max_tool_rounds)
                actual = normalized_query_result(answer.query_result)
                metrics = {**path_metrics(answer, question), "accurate": actual == expected,
                           "completed": answer.completed, "expected_result": expected, "actual_result": actual}
                result = QuestionResult(question["id"], variant, repetition, actual == expected, answer.completed,
                                        metrics, answer)
                results.append(result)
                LOGGER.info("%s %s #%d: %s", question["id"], variant, repetition,
                            "accurate" if result.accurate else answer.termination)
                if on_result is not None:
                    on_result(result)
            if stopped:
                break
        if stopped:
            break
    return EvaluationReport(suite["id"], suite["version"], model, results, aggregate(results), stopped)
