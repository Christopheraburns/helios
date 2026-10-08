"""CR-0d: the access check's verdicts, against fake Impala and S3."""

import io

from apps.helios.crawler.access_check import run

ARTIFACT = {
    "connector_type": "helios_ds_s3",
    "bucket": "b",
    "key": "helios-db/source/datasets/d1/artifacts/a.pdf",
}


class FakeCursor:
    def __init__(self, allowed):
        self.allowed = allowed
        self.result = []

    def execute(self, sql):
        if "helios_index" in sql:
            if "helios_index" not in self.allowed:
                raise RuntimeError("AuthorizationException: no privileges on helios_index")
            self.result = [(1,)]
            return
        if "EFFECTIVE_USER" in sql:
            self.result = [("srv_helios_crawler",)]
            return
        table = sql.split(" FROM ")[1].split()[0]
        if table not in self.allowed:
            raise RuntimeError(f"AuthorizationException: User does not have privileges on {table}")
        import json

        self.result = [("d1", json.dumps(ARTIFACT))]

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result


class FakeS3:
    def __init__(self, readable):
        self.readable = readable

    def get_object(self, Bucket, Key):
        if not any(Key.startswith(p) for p in self.readable):
            raise RuntimeError("An error occurred (AccessDenied) when calling GetObject")
        return {"Body": io.BytesIO(b"x")}


def test_correct_isolation_passes():
    user, checks = run(
        lambda: FakeCursor({"helios_ds.crawlable_artifacts", "tpcds.customer", "helios_index"}),
        FakeS3(["helios-db/source/datasets/"]),
        "tpcds.customer",
    )
    assert user == "srv_helios_crawler"
    assert all(c.ok for c in checks), [(c.name, c.outcome) for c in checks]


def test_ground_truth_access_and_manifest_access_fail_the_check():
    everything = {
        "helios_ds.crawlable_artifacts",
        "tpcds.customer",
        "helios_ground_truth.claims",
        "helios_ground_truth.entity_mentions",
        "helios_ground_truth.expected_queries",
        "helios_ds.artifacts",
        "helios_ds.scenario_plans",
    }
    _, checks = run(lambda: FakeCursor(everything), FakeS3(["helios-db/source/"]), "tpcds.customer")
    failed = {c.name for c in checks if not c.ok}
    assert "SELECT from helios_ground_truth.claims" in failed
    assert "read a generation manifest" in failed


def test_the_warehouse_table_comes_from_the_caller_and_is_reported_when_absent():
    allowed = {"helios_ds.crawlable_artifacts", "clinic.visits", "helios_index"}
    _, checks = run(lambda: FakeCursor(allowed), FakeS3(["helios-db/source/datasets/"]), "clinic.visits")
    assert next(c for c in checks if c.name == "SELECT from clinic.visits").ok
    _, checks = run(lambda: FakeCursor(allowed), FakeS3(["helios-db/source/datasets/"]))
    missing = next(c for c in checks if c.name == "SELECT from a warehouse table")
    assert not missing.ok and "no mapping is active" in missing.outcome
    _, checks = run(lambda: FakeCursor(allowed), FakeS3([]), "clinic.visits; DROP TABLE x")
    assert all("DROP" not in c.name for c in checks)
