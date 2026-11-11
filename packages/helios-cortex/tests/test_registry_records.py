"""Registry records carry no ownership: who may see a model is the host's decision (burndown 2.6)."""
import pytest

from helios_core.domain import DataSource, DataSourceReference, Model


def warehouse(source_id="warehouse"):
    return DataSource(source_id, "Warehouse", "impala", "default")


def test_a_model_resolves_the_data_sources_it_references():
    model = Model("sales", "Sales", (DataSourceReference("warehouse", ("tpcds.store_sales",)),))
    assert model.resolve_data_sources({"warehouse": warehouse(), "other": warehouse("other")}) == (warehouse(),)
    with pytest.raises(ValueError, match="unknown data source 'warehouse'"):
        model.resolve_data_sources({})


def test_records_reject_empty_and_duplicate_values():
    with pytest.raises(ValueError, match="at least one data source"):
        Model("sales", "Sales", ())
    with pytest.raises(ValueError, match="duplicates"):
        Model("sales", "Sales", (DataSourceReference("w"), DataSourceReference("w")))
    with pytest.raises(ValueError, match="connection_ref"):
        DataSource("w", "W", "impala", " ")
