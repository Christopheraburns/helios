from pathlib import Path

from apps.console.evaluation import (
    _aggregate_metrics,
    _normalized_result,
    list_suites,
    load_suite,
    save_suite,
)
from helios_core.metadata import EvaluationResult
from helios_core.ossie import SemanticModel


def result(
    identifier: str,
    variant: str,
    *,
    accurate: bool,
    completed: bool,
    metrics: dict,
) -> EvaluationResult:
    return EvaluationResult(
        id=identifier,
        evaluation_run_id="evaluation-1",
        question_id="Q1",
        variant=variant,
        repetition=1,
        trace_run_id=f"trace-{identifier}",
        accurate=accurate,
        completed=completed,
        metrics=metrics,
    )


def test_normalized_results_ignore_row_order_and_value_types():
    left = _normalized_result(["REGION", "COUNT"], [["west", 2], ["east", 1]])
    right = _normalized_result(["region", "count"], [["east", "1"], ["west", "2"]])

    assert left == right


def test_evaluation_metrics_measure_accuracy_efficiency_and_recovery():
    metrics = _aggregate_metrics(
        [
            result(
                "baseline",
                "baseline",
                accurate=True,
                completed=True,
                metrics={
                    "tool_calls": 2,
                    "excess_tool_calls": 0,
                    "invalid_calls": 0,
                    "redundant_calls": 0,
                    "recovery_attempts": 0,
                    "recovered": 0,
                    "grounded_calls": 2,
                    "tokens_in": 80,
                    "tokens_out": 20,
                    "duration_ms": 500,
                },
            ),
            result(
                "candidate",
                "candidate",
                accurate=False,
                completed=False,
                metrics={
                    "tool_calls": 4,
                    "excess_tool_calls": 2,
                    "invalid_calls": 1,
                    "redundant_calls": 1,
                    "recovery_attempts": 1,
                    "recovered": 0,
                    "grounded_calls": 1,
                    "tokens_in": 160,
                    "tokens_out": 40,
                    "duration_ms": 1_000,
                },
            ),
        ],
        repository=None,
    )

    assert metrics["baseline"]["answer_accuracy"] == 1
    assert metrics["baseline"]["tokens_per_correct_answer"] == 100
    assert metrics["candidate"]["completion_rate"] == 0
    assert metrics["candidate"]["invalid_calls"] == 1
    assert metrics["candidate"]["invalid_call_rate"] == 0.25
    assert metrics["candidate"]["redundant_calls"] == 1
    assert metrics["candidate"]["redundant_call_rate"] == 0.25
    assert metrics["candidate"]["grounding_rate"] == 0.25


def test_imported_versioned_suite_is_listed_and_loadable(tmp_path, monkeypatch):
    monkeypatch.setenv("HELIOS_ROOT", str(tmp_path))
    document = {
        "id": "smoke",
        "version": "1.0.0",
        "questions": [{
            "id": "Q1",
            "question": "Count customers",
            "reference_sql": "SELECT COUNT(*) FROM customers",
            "minimum_tools": ["search_semantics", "run_query"],
            "grounding": ["customer_count"],
        }],
    }

    save_suite(document)

    assert load_suite("smoke", "1.0.0") == document
    assert ("smoke", "1.0.0") in {
        (suite["id"], suite["version"]) for suite in list_suites()
    }


def test_tpcds_suite_grounding_resolves_in_published_model():
    root = Path(__file__).resolve().parents[1]
    model = SemanticModel.load(
        str(root / "models/published/tpcds.ossie.yaml")
    )

    for question in load_suite("tpcds")["questions"]:
        for name in question["grounding"]:
            assert name in model.metrics or model.field(name)
