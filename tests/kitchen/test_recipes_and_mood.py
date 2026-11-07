from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.kitchen import mood as moods
from services.kitchen.errors import KitchenError
from services.kitchen.recipes import RECIPES, expand, shortfall, sufficient
from services.kitchen.settings import Settings


def test_settings_read_typed_environment_and_reject_bad_values(monkeypatch):
    run_id = uuid4()
    monkeypatch.setenv("PIZZAOPS_RUN_ID", str(run_id))
    monkeypatch.setenv("KITCHEN_INVENTORY_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("KITCHEN_WORKERS", "3")
    monkeypatch.setenv("KITCHEN_COOKING_S", "0.5")
    monkeypatch.setenv("KITCHEN_ATTEMPT_TIMEOUT_S", "1")
    settings = Settings.from_env()
    assert (settings.run_id, settings.inventory_url, settings.workers) == (run_id, "http://127.0.0.1:9999", 3)
    assert settings.cooking_s == 0.5 and settings.attempt_timeout_s == 1.0
    monkeypatch.setenv("KITCHEN_WORKERS", "0")
    with pytest.raises(ValidationError):
        Settings.from_env()
    with pytest.raises(ValidationError):
        Settings(initial_trust=101)
    with pytest.raises(ValidationError):
        Settings(attempt_timeout_s=0)


def test_recipes_expand_by_quantity_and_cheese_free_pizza_exists():
    assert expand("PEPPERONI", 3) == {"DOUGH": 3, "SAUCE": 3, "CHEESE": 3, "PEPPERONI": 3}
    assert expand("MARINARA", 1) == {"DOUGH": 1, "SAUCE": 1, "BASIL": 1}
    assert "CHEESE" not in RECIPES["MARINARA"]
    assert all(all(units >= 1 for units in recipe.values()) for recipe in RECIPES.values())


@pytest.mark.parametrize("pizza, quantity, code", [("HAWAIIAN", 1, "UNKNOWN_PIZZA"), ("cheese", 1, "UNKNOWN_PIZZA"),
                                                   ("CHEESE", 0, "INVALID_REQUEST"), ("CHEESE", True, "INVALID_REQUEST"),
                                                   ("CHEESE", 1.0, "INVALID_REQUEST")])
def test_unknown_pizza_and_bad_quantity_are_rejected(pizza, quantity, code):
    with pytest.raises(KitchenError) as caught:
        expand(pizza, quantity)
    assert caught.value.status == 400 and caught.value.code == code


def test_shortfall_reports_only_missing_amounts():
    needed = {"DOUGH": 2, "CHEESE": 2, "BASIL": 1}
    assert shortfall({"DOUGH": 2, "CHEESE": 1}, needed) == {"CHEESE": 1, "BASIL": 1}
    assert sufficient({"DOUGH": 2, "CHEESE": 2, "BASIL": 5}, needed)
    assert not sufficient({}, needed)


def test_initial_mood_and_labels():
    state = moods.initial(85, 60)
    assert (state.patience, state.trust, state.stress, state.failed_reservation_count, state.version) == (85, 60, 0, 0, 1)
    assert state.mood == "CALM"
    assert moods.label(24, 100, 0) == "FURIOUS"
    assert moods.label(80, 49, 0) == "SUSPICIOUS"
    assert moods.label(59, 60, 0) == "TENSE"
    assert moods.label(85, 60, 50) == "TENSE"
    assert moods.label(24, 20, 90) == "FURIOUS"


def test_one_confirmed_lie_takes_trust_below_the_authoritative_threshold():
    state = moods.initial(85, 60)
    assert not moods.demands_authoritative(state)
    lied = moods.stale_contradiction(state)
    assert (lied.trust, lied.patience, lied.failed_reservation_count, lied.version) == (35, 75, 1, 2)
    assert lied.mood == "SUSPICIOUS" and moods.demands_authoritative(lied)
    recovered = lied
    for _ in range(3):
        recovered = moods.reservation_succeeded(recovered)
    assert recovered.trust == 50 and not moods.demands_authoritative(recovered)


def test_transitions_clamp_and_return_same_state_when_nothing_changes():
    calm = moods.initial(100, 100)
    assert moods.adjust(calm, stress=-50) is calm
    assert moods.reservation_succeeded(calm) is calm
    assert moods.reconcile(calm, 15, 10) is calm
    floored = moods.adjust(moods.initial(0, 0), patience=-10, trust=-10)
    assert (floored.patience, floored.trust) == (0, 0)
    assert moods.dependency_trouble(floored).stress == 10
    assert moods.shortage(floored).failed_reservation_count == 1
    stressed = moods.adjust(moods.initial(85, 60), stress=200)
    assert stressed.stress == 100


def test_reconcile_recovers_patience_and_trust_without_exceeding_limits():
    worn = moods.adjust(moods.initial(85, 60), patience=-70, trust=-30, stress=60)
    assert (worn.patience, worn.trust, worn.stress, worn.mood) == (15, 30, 60, "FURIOUS")
    assert moods.exhausted(worn)
    recovered = moods.reconcile(worn, 15, 10)
    assert (recovered.patience, recovered.trust, recovered.stress) == (30, 40, 40)
    assert recovered.mood == "SUSPICIOUS"
    capped = moods.reconcile(moods.initial(95, 95), 15, 10)
    assert (capped.patience, capped.trust, capped.version) == (100, 100, 2)
