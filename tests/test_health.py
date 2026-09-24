from types import SimpleNamespace

from helios_core import health


class Metadata:
    def schema_version(self):
        return 1


def test_infrastructure_health_is_independent_and_redacts_failures(
    monkeypatch,
):
    monkeypatch.setattr(
        health,
        "atlas_config",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        health,
        "AtlasClient",
        lambda _: SimpleNamespace(
            ping=lambda: (_ for _ in ()).throw(
                RuntimeError("https://secret-host/token")
            )
        ),
    )
    monkeypatch.setattr(health, "impala_config", lambda: None)
    monkeypatch.setattr(
        health,
        "llm_from_env",
        lambda: SimpleNamespace(ping=lambda: True),
    )

    components = health.collect_infrastructure_status(Metadata())

    assert [component.status for component in components] == [
        "healthy",
        "unavailable",
        "unknown",
        "healthy",
    ]
    assert components[1].description == "Atlas connectivity check failed."
    assert "secret-host" not in repr(components)
    assert health.aggregate_status(components) == "degraded"
