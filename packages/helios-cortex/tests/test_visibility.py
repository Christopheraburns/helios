"""ModelVisibility: the host decides which models a caller may list and use (burndown 2.4)."""
import pytest

from helios_cortex import Caller
from helios_cortex.visibility import (
    AllModels,
    ModelNotVisible,
    require_visible,
    visible_models,
)

MODELS = ["sales", "finance", "hr"]


class ByTeam:
    """A host rule: models are visible to the teams listed for them in the caller's attributes."""

    teams = {"sales": {"sales", "exec"}, "finance": {"finance", "exec"}, "hr": {"hr"}}

    def can_see(self, caller, model_id):
        return caller.attributes.get("team") in self.teams[model_id]


def test_by_default_every_model_is_visible():
    assert visible_models(AllModels(), Caller(), MODELS) == MODELS
    assert require_visible(AllModels(), Caller(), "hr") == "hr"


def test_the_host_rule_filters_the_list_in_order():
    assert visible_models(ByTeam(), Caller("ann", attributes={"team": "exec"}), MODELS) == ["sales", "finance"]
    assert visible_models(ByTeam(), Caller("bob", attributes={"team": "hr"}), MODELS) == ["hr"]
    assert visible_models(ByTeam(), Caller(), MODELS) == []


def test_a_hidden_model_reads_as_not_found():
    with pytest.raises(LookupError, match="^model 'hr' not found$") as raised:
        require_visible(ByTeam(), Caller("ann", attributes={"team": "exec"}), "hr")
    assert isinstance(raised.value, ModelNotVisible)
    assert raised.value.model_id == "hr"
    assert str(raised.value) == str(ModelNotVisible("hr"))
