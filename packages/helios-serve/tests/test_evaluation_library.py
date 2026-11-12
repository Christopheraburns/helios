"""helios_serve.evaluation (burndown 6.5): suites, the agent loop calling the tools
directly, scoring against the reference SQL, path metrics, aggregation and ``helios
eval``. The LLM is scripted; the warehouse is test_tools' DuckDB."""
import io
import json

import pytest

pytest.importorskip("httpx", reason="the evaluation's LLM client needs helios-serve[eval]")

from helios_cortex.llm import LLMError, ToolCall, ToolTurn  # noqa: E402
from helios_serve import evaluation  # noqa: E402
from helios_serve.evaluation import (  # noqa: E402
    Answer,
    EvaluationError,
    ToolCallRecord,
    aggregate,
    answer_question,
    path_metrics,
    run_evaluation,
)

from test_tools import ALICE, publish  # noqa: E402

REFERENCE = ("SELECT c.c_region, SUM(o.o_amount) AS revenue FROM shop.orders o "
             "JOIN shop.customers c ON o.o_customer_id = c.c_customer_id GROUP BY 1")
QUESTION = {"id": "Q1", "question": "What is revenue by region?", "reference_sql": REFERENCE,
            "minimum_tools": ["search_semantics", "run_query"], "grounding": ["revenue"]}
SUITE = {"id": "shop", "version": "1.0.0", "model_id": "shop", "questions": [QUESTION]}


class Scripted:
    """An LLM that makes the given tool calls, one list per round, then answers."""

    model = "scripted"

    def __init__(self, *rounds, answer="Revenue is 3750 in the east and 3825 in the west."):
        self.rounds = list(rounds)
        self.answer = answer
        self.seen = []

    def tool_turn(self, system, messages, tools):
        self.seen.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.rounds:
            return ToolTurn(self.answer, (), "end_turn", tokens_in=10, tokens_out=5)
        calls = self.rounds.pop(0)
        return ToolTurn("", tuple(ToolCall(f"c{i}", name, args) if isinstance(args, dict)
                                  else ToolCall(f"c{i}", name, {}, parse_error=args)
                                  for i, (name, args) in enumerate(calls)), "tool_use", tokens_in=10, tokens_out=5)


SEARCH = ("search_semantics", {"question": "revenue by region"})
RUN = ("run_query", {"metrics": ["revenue"], "dimensions": ["shop.customers.c_region"]})


def good():
    return Scripted([SEARCH], [RUN])


def wrong():
    return Scripted([SEARCH], [("run_query", {"metrics": ["revenue"]})])     # no regions: the wrong answer


# ---------------------------------------------------------------------------- suites
def test_the_builtin_suite_ships_in_the_package_and_saved_suites_join_it(tmp_path):
    [builtin] = evaluation.list_suites()
    assert (builtin["id"], builtin["version"], len(builtin["questions"])) == ("tpcds", "1.0.0", 20)
    evaluation.save_suite(SUITE, tmp_path)
    assert (tmp_path / "shop-1.0.0.json").is_file()
    assert evaluation.load_suite("shop", directory=tmp_path) == SUITE
    assert [s["id"] for s in evaluation.list_suites(tmp_path)] == ["shop", "tpcds"]
    evaluation.save_suite({**builtin, "questions": builtin["questions"][:1]}, tmp_path)
    assert len(evaluation.load_suite("tpcds", "1.0.0", directory=tmp_path)["questions"]) == 20   # built-in wins
    with pytest.raises(LookupError):
        evaluation.load_suite("shop", "2.0.0", directory=tmp_path)


@pytest.mark.parametrize("change, message", [
    ({"id": ""}, "id"), ({"questions": []}, "1 to 100"),
    ({"questions": [QUESTION, QUESTION]}, "unique"),
    ({"questions": [{**QUESTION, "reference_sql": " "}]}, "reference_sql"),
    ({"questions": [{**QUESTION, "grounding": []}]}, "grounding"),
])
def test_an_invalid_suite_is_refused(change, message, tmp_path):
    with pytest.raises(ValueError, match=message):
        evaluation.save_suite({**SUITE, **change}, tmp_path)
    (tmp_path / "bad.json").write_text(json.dumps({**SUITE, **change}))
    with pytest.raises(EvaluationError, match=message):
        evaluation.read_suite(tmp_path / "bad.json")


def test_results_compare_without_row_order_case_or_value_types():
    assert evaluation.normalized_result(["REGION", "N"], [["west", 2], ["east", None]]) == \
        evaluation.normalized_result(["region", "n"], [["east", None], ["west", "2"]])


# ---------------------------------------------------------------------------- answering
def test_the_llm_answers_through_the_tools_called_directly(stores):
    llm = good()
    answer = answer_question(llm, QUESTION["question"], stores=stores, caller=ALICE, model="shop")
    assert answer.completed and answer.termination == "final_answer" and "3825" in answer.text
    assert [c.tool for c in answer.tool_calls] == ["search_semantics", "run_query"]
    assert all(c.arguments["model"] == "shop" for c in answer.tool_calls)          # pinned to the model
    assert answer.query_result["columns"] == ["c_region", "revenue"]
    assert (answer.tokens_in, answer.tokens_out) == (30, 15)
    offered = {t["name"] for t in llm.seen[0]["tools"]}
    assert {"search_semantics", "run_query", "compile_query"} <= offered
    assert all(t["inputSchema"]["type"] == "object" for t in llm.seen[0]["tools"])
    assert "'shop'" in llm.seen[0]["system"]
    tool_message = llm.seen[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "matches" in json.loads(tool_message["content"])
    assert stores.audit.records[-1].action == "run_query"                          # audited like any call


def test_invalid_calls_are_answered_and_a_repeated_failure_ends_the_answer(stores):
    llm = Scripted([("drop_table", {}), ("run_query", "not JSON")], [("describe", {"name": "weather"})],
                   [("describe", {"name": "weather"})])
    answer = answer_question(llm, "?", stores=stores, model="shop")
    assert [c.error for c in answer.tool_calls] == ["unknown_tool", "invalid_tool_arguments",
                                                    "semantic_not_found", "semantic_not_found"]
    assert answer.termination == "tool_error" and not answer.completed
    assert "recovery" in answer.tool_calls[2].result
    assert path_metrics(answer, QUESTION)["invalid_calls"] == 2


def test_an_argument_the_tool_does_not_take_is_an_invalid_call(stores):
    answer = answer_question(Scripted([("list_models", {"sql": "DROP TABLE x"})]), "?", stores=stores)
    assert answer.tool_calls[0].error == "invalid_tool_arguments" and answer.termination == "tool_error"


def test_the_loop_stops_at_its_round_limit_and_on_an_llm_error(stores):
    endless = Scripted(*[[SEARCH]] * 10)
    answer = answer_question(endless, "?", stores=stores, max_tool_rounds=3)
    assert answer.termination == "tool_round_limit" and len(answer.tool_calls) == 3

    class Down:
        def tool_turn(self, *args):
            raise LLMError("anthropic 529: overloaded")

    assert answer_question(Down(), "?", stores=stores).termination == "llm_error"
    assert answer_question(Scripted(answer=" "), "?", stores=stores).termination == "empty_answer"


def test_path_metrics():
    calls = [ToolCallRecord("search_semantics", {"question": "revenue"}, {"matches": []}),
             ToolCallRecord("run_query", {"metrics": ["profit"]}, {"error": "invalid_semantic_query"}),
             ToolCallRecord("run_query", {"metrics": ["revenue"]}, {"rows": []}),
             ToolCallRecord("run_query", {"metrics": ["revenue"]}, {"rows": []})]
    metrics = path_metrics(Answer("x", "final_answer", calls, 7, 3, 12.0), QUESTION)
    assert metrics == {"tool_calls": 4, "excess_tool_calls": 2, "invalid_calls": 0, "redundant_calls": 1,
                       "recovery_attempts": 1, "recovered": 1, "grounded_calls": 3, "tokens_in": 7,
                       "tokens_out": 3, "duration_ms": 12.0}


# ---------------------------------------------------------------------------- running a suite
def test_a_suite_scores_each_variant_against_the_reference(stores):
    seen = []
    report = run_evaluation(SUITE, stores, {"baseline": good(), "candidate": wrong()}, caller=ALICE,
                            on_result=seen.append)
    assert (report.suite_id, report.model, report.cancelled) == ("shop", "shop", False)
    assert [(r.variant, r.accurate, r.completed) for r in report.results] == [
        ("baseline", True, True), ("candidate", False, True)]
    assert seen == report.results
    assert report.metrics["baseline"]["answer_accuracy"] == 1 and report.metrics["candidate"]["answer_accuracy"] == 0
    assert report.results[0].metrics["expected_result"] == report.results[0].metrics["actual_result"]
    assert json.loads(json.dumps(report.as_dict(), default=str))["results"][0]["answer"]["termination"] == \
        "final_answer"


def test_repetitions_and_cancelling(stores):
    report = run_evaluation(SUITE, stores, {"baseline": Scripted([SEARCH], [RUN], [SEARCH], [RUN])},
                            repetitions=2)
    assert [r.repetition for r in report.results] == [1, 2]
    asked = iter([False, True])
    cancelled = run_evaluation(SUITE, stores, {"a": good(), "b": good()}, cancelled=lambda: next(asked))
    assert cancelled.cancelled and [r.variant for r in cancelled.results] == ["a"]


def test_a_failing_reference_query_or_a_bad_run_is_an_evaluation_error(stores):
    broken = {**SUITE, "questions": [{**QUESTION, "reference_sql": "SELECT * FROM shop.nowhere"}]}
    with pytest.raises(EvaluationError, match="reference query failed"):
        run_evaluation(broken, stores, {"baseline": good()})
    with pytest.raises(EvaluationError, match="variant"):
        run_evaluation(SUITE, stores, {})
    with pytest.raises(EvaluationError, match="questions"):
        run_evaluation({**SUITE, "questions": []}, stores, {"baseline": good()})


def test_aggregate_takes_any_result_with_the_four_fields():
    class Row:
        def __init__(self, variant, accurate, metrics):
            self.variant, self.accurate, self.completed, self.metrics = variant, accurate, accurate, metrics

    metrics = aggregate([Row("a", True, {"tool_calls": 2, "tokens_in": 80, "tokens_out": 20}),
                         Row("a", False, {"tool_calls": 2, "invalid_calls": 1})])
    assert metrics["a"]["answer_accuracy"] == 0.5 and metrics["a"]["invalid_call_rate"] == 0.25
    assert metrics["a"]["tokens_per_correct_answer"] == 100


# ---------------------------------------------------------------------------- helios eval
@pytest.fixture
def configured(tmp_path, warehouse, monkeypatch):
    """A helios.toml for the DuckDB warehouse, with the shop model registered and published."""
    from helios_cortex.artifacts import ArtifactStore
    from helios_cortex.domain import DataSource, DataSourceReference, Model
    from helios_cortex.registry import SqlRegistry
    from helios_cortex.settings import Settings

    config = tmp_path / "helios.toml"
    config.write_text(f'data_dir = "{tmp_path / "data"}"\n\n[connections.default]\nengine = "duckdb"\n'
                      f'path = "{warehouse}"\n\n[llm]\nprovider = "anthropic"\napi_key = "test-key"\n')
    settings = Settings.from_file(config)
    registry = SqlRegistry.from_settings(settings)
    registry.save_data_source(DataSource("warehouse", "Warehouse", "duckdb", "default"))
    registry.save_model(Model("shop", "Shop", (DataSourceReference("warehouse"),)), status="published")
    publish(ArtifactStore.from_settings(settings))
    evaluation.save_suite(SUITE, evaluation.suite_directory(settings))
    llms = {}

    def scripted(llm):
        llms[llm.model] = good() if llm.model != "worse" else wrong()
        return llms[llm.model]

    monkeypatch.setattr("helios_cortex.llm.llm_from_settings", scripted)
    return config


def helios(*argv):
    from helios_cortex.cli import main

    out, err = io.StringIO(), io.StringIO()
    return main(list(argv), out=out, stderr=err), out.getvalue(), err.getvalue()


def test_helios_eval_lists_runs_and_compares_suites(configured):
    status, out, _ = helios("--config", str(configured), "eval", "--list")
    assert status == 0 and "shop  1.0.0  1 questions  (model shop)" in out and "tpcds  1.0.0" in out

    status, out, err = helios("--config", str(configured), "eval", "shop", "--candidate-model", "worse")
    assert status == 0, err
    assert "ok   Q1 baseline #1" in out and "MISS Q1 candidate #1" in out
    assert "baseline (" in out and "accuracy 100%" in out and "accuracy 0%" in out

    status, out, _ = helios("--config", str(configured), "eval", "shop", "--json")
    assert json.loads(out)["metrics"]["baseline"]["answer_accuracy"] == 1


def test_single_user_policy_allows_service_queries_and_leaves_callers_to_the_warehouse():
    from helios_cortex import Caller, IdentityMode
    from helios_cortex.data_authorization import DataAction, DataResource
    from helios_serve import single_user_policy

    policy = single_user_policy()
    service = DataResource("warehouse", "shop.orders", IdentityMode.SERVICE)
    assert policy.can_access(Caller(), service, DataAction.QUERY_EXECUTE).allowed
    assert not policy.can_access(Caller(), service, DataAction.PROFILE).allowed
    as_caller = DataResource("warehouse", "shop.orders", IdentityMode.IMPERSONATE)
    assert policy.can_access(Caller("alice"), as_caller, DataAction.QUERY_EXECUTE).allowed
    assert not policy.can_access(Caller(), as_caller, DataAction.QUERY_EXECUTE).allowed


def test_helios_eval_as_a_caller_keeps_the_default_policy(configured):
    """--as alice on a service-identity connection: the warehouse cannot enforce alice, so no query runs."""
    status, out, _ = helios("--config", str(configured), "eval", "shop", "--as", "alice")
    assert status == 0 and "MISS Q1 baseline #1" in out


def test_helios_eval_reports_what_the_user_can_fix(configured, tmp_path):
    status, _, err = helios("--config", str(configured), "eval", "nothing")
    assert status == 1 and "not found" in err
    status, _, err = helios("--config", str(configured), "eval", "shop", "--question", "Q9")
    assert status == 1 and "no question Q9" in err
    (tmp_path / "bad.json").write_text("{")
    status, _, err = helios("--config", str(configured), "eval", "--suite-file", str(tmp_path / "bad.json"))
    assert status == 1 and "cannot read the suite" in err
