import pytest

from helios_core.domain import DataSource, DataSourceReference, Model, Organization


def source(source_id: str = "warehouse") -> DataSource:
    return DataSource(
        id=source_id,
        organization_id="acme",
        name="Production warehouse",
        connector="impala",
        connection_ref="connections/production",
        snapshot_ids=("snapshot-2026-09-22",),
    )


def model(model_id: str, data_source_id: str = "warehouse") -> Model:
    return Model(
        id=model_id,
        organization_id="acme",
        name=model_id.replace("-", " ").title(),
        data_sources=(
            DataSourceReference(
                data_source_id=data_source_id,
                selected_assets=("sales.orders", "sales.customers"),
            ),
        ),
    )


def test_multiple_models_can_share_a_data_source_without_owning_snapshots():
    warehouse = source()
    sales = model("sales-model")
    support = model("support-model")

    assert sales.resolve_data_sources({warehouse.id: warehouse}) == (warehouse,)
    assert support.resolve_data_sources({warehouse.id: warehouse}) == (warehouse,)
    assert sales.data_sources[0].selected_assets == (
        "sales.orders",
        "sales.customers",
    )
    assert warehouse.snapshot_ids == ("snapshot-2026-09-22",)


def test_model_can_reference_multiple_data_sources_and_child_resources():
    warehouse = source()
    lakehouse = source("lakehouse")
    customer_model = Model(
        id="customer-360",
        organization_id="acme",
        name="Customer 360",
        data_sources=(
            DataSourceReference("warehouse", ("crm.customers",)),
            DataSourceReference("lakehouse", ("events.web_sessions",)),
        ),
        version_ids=("v1", "v2"),
        discovery_run_ids=("run-1",),
        glossary_id="glossary-1",
        semantic_model_id="semantic-model-v2",
        ontology_id="ontology-v1",
    )

    assert customer_model.resolve_data_sources(
        {"warehouse": warehouse, "lakehouse": lakehouse}
    ) == (warehouse, lakehouse)


def test_organization_validates_ownership_and_references():
    organization = Organization("acme", "Acme", member_ids=("user-1",))
    warehouse = source()

    organization.validate_resources([warehouse], [model("sales-model")])

    other_source = DataSource(
        id="other-warehouse",
        organization_id="other",
        name="Other warehouse",
        connector="hive",
        connection_ref="connections/other",
    )
    cross_org_model = model("invalid-model", other_source.id)
    with pytest.raises(ValueError, match="belongs to organization"):
        organization.validate_resources([other_source], [cross_org_model])


def test_model_rejects_missing_or_duplicate_data_source_references():
    with pytest.raises(ValueError, match="at least one"):
        Model(id="empty", organization_id="acme", name="Empty", data_sources=())

    reference = DataSourceReference("warehouse")
    with pytest.raises(ValueError, match="duplicates"):
        Model(
            id="duplicate",
            organization_id="acme",
            name="Duplicate",
            data_sources=(reference, reference),
        )


def test_model_cannot_resolve_a_data_source_from_another_organization():
    other_source = DataSource(
        id="warehouse",
        organization_id="other",
        name="Other warehouse",
        connector="hive",
        connection_ref="connections/other",
    )

    with pytest.raises(ValueError, match="another organization"):
        model("sales-model").resolve_data_sources({"warehouse": other_source})
