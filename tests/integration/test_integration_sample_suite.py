"""The sample's evaluation suite (burndown 9.3) against the sample it was written for: each
question's reference SQL gives exactly the answer that running its grounding through
helios-serve's run_query gives, compared as the evaluation compares them. So a perfect
agent scores 100%, and a wrong reference cannot mark a right answer wrong. Also: the
suite helios init --sample writes is this one, and helios eval reads it by file."""
import json

from helios_cortex import sample as helios_sample
from helios_serve import Stores, evaluation, tools
from helios_serve.stores import single_user_policy


def test_every_reference_answer_is_what_its_grounding_answers(sample, settings):
    suite = evaluation.read_suite(sample.suite)
    assert suite == helios_sample.suite_document() and suite["model_id"] == "tpcds"
    stores = Stores.from_settings(settings, data_policy=single_user_policy())
    model = stores.models.load("tpcds")[1]
    for question in suite["questions"]:
        metrics = [name for name in question["grounding"] if name in model.metrics]
        dimensions = [name for name in question["grounding"] if name not in model.metrics]
        answered = tools.run_query(None, stores, metrics=metrics, dimensions=dimensions, limit=1000)
        assert "rows" in answered, (question["id"], answered)
        reference = evaluation.reference_result(question["reference_sql"], stores=stores, model=suite["model_id"])
        assert evaluation.normalized_query_result(answered) == reference, question["id"]


def test_helios_eval_reads_the_sample_suite_by_file(sample, monkeypatch):
    import io

    from helios_cortex.cli import main

    monkeypatch.chdir(sample.config.parent)
    out, err = io.StringIO(), io.StringIO()
    status = main(["eval", "--list"], out=out, stderr=err)
    assert status == 0 and out.getvalue() == ""                    # nothing saved, nothing built in
    status = main(["eval"], out=out, stderr=err)
    assert status == 1 and "sample-suite.json" in err.getvalue()
    assert json.loads(sample.suite.read_text())["id"] == "tpcds-sample"
