"""The conformance suite fails backends that break the contract, and a third-party
backend can run it from its own tests (burndown 4.5)."""
import subprocess
import sys
import textwrap

BROKEN = textwrap.dedent('''
    import time

    import pytest

    from helios_core.artifact_storage import LocalStorage
    from helios_core.conformance import ArtifactStorageConformance, RegistryConformance
    from helios_core.registry import InMemoryRegistry


    class CheckThenWrite(LocalStorage):
        """The usual mistake: create checks for the key, then writes it, not atomically."""

        def create(self, key, data):
            if self.exists(key):
                return False
            time.sleep(0.01)                  # a store with real latency between the two calls
            self.write_bytes(key, data)
            return True


    class DeletesInUse(InMemoryRegistry):
        def delete_data_source(self, data_source_id):
            with self._lock:
                return self._data_sources.pop(data_source_id, None) is not None


    class TestCheckThenWrite(ArtifactStorageConformance):
        @pytest.fixture
        def storage(self, tmp_path):
            return CheckThenWrite(tmp_path)


    class TestDeletesInUse(RegistryConformance):
        @pytest.fixture
        def registry(self):
            return DeletesInUse()


    class TestReference(RegistryConformance):
        @pytest.fixture
        def registry(self):
            return InMemoryRegistry()
''')


def test_the_suite_fails_backends_that_break_the_contract(tmp_path):
    (tmp_path / "test_backends.py").write_text(BROKEN)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=300)
    failed = sorted(line.split("::", 1)[1].split(" ")[0] for line in result.stdout.splitlines()
                    if line.startswith("FAILED"))

    # Racing creators all "win"; so do racing activations and publishers built on create.
    assert "TestCheckThenWrite::test_exactly_one_of_many_racing_creates_wins" in failed
    assert "TestCheckThenWrite::test_a_stale_or_racing_activation_is_a_conflict" in failed
    assert "TestDeletesInUse::test_a_data_source_in_use_cannot_be_deleted" in failed
    assert not any(name.startswith("TestReference::") for name in failed)
    # Every other test passed: the suite finds what is wrong and only that.
    assert all(name.split("::")[1] in {
        "test_exactly_one_of_many_racing_creates_wins",
        "test_a_stale_or_racing_activation_is_a_conflict",
        "test_racing_publishers_each_get_their_own_activation",
        "test_a_data_source_in_use_cannot_be_deleted",
    } for name in failed), failed
