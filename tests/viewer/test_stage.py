"""The stage acts events out in a believable order without ever changing what happened."""

from itertools import count

import pytest

from tests.viewer.harness import advance, event, job_record, mood, order_record
from viewer import dialogue
from viewer.stage import HANDOFF_WAIT_S
from viewer.world import World

ORDER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


class Shift:
    """A world plus per-service sequence numbers, so tests read like the event stream."""

    def __init__(self):
        self.world = World()
        self.sequences = {name: count(1) for name in ("order", "kitchen", "inventory")}

    def order(self, kind, status, order_id=ORDER, version=1, **fields):
        record = order_record(order_id, status=status, version=version, **fields)
        self.world.apply(event("order", kind, next(self.sequences["order"]), record, order_id, entity_version=version))

    def cook(self, kind, status, order_id=ORDER, version=1):
        record = job_record(order_id, status=status, version=version, pizza="PEPPERONI")
        self.world.apply(event("kitchen", kind, next(self.sequences["kitchen"]), record, order_id, entity_version=version))

    def watch(self, seconds, step=0.05):
        """Tick, collecting cues and whether each customer was ever seen carrying a box."""
        cues, carried = [], set()
        for _ in range(round(seconds / step)):
            self.world.tick(step)
            cues += [name for name, _, _ in self.world.stage.take_cues()]
            carried |= {order_id for order_id, customer in self.world.customers.items() if customer.carrying}
        return cues, carried


def test_nobody_holds_a_box_before_their_order_is_ready():
    shift = Shift()
    shift.order("ORDER_RECEIVED", "RECEIVED")
    shift.cook("COOK_STARTED", "COOKING")
    shift.watch(3)
    shift.cook("COOK_COMPLETED", "COOKED", version=2)
    _, carried = shift.watch(15)
    assert shift.world.stage.tickets[ORDER].box == "counter"  # the chef set it down...
    assert not carried  # ...but Order has not said READY, so it stays there
    shift.order("ORDER_READY", "READY", version=2)
    _, carried = shift.watch(15)
    assert ORDER in carried and ORDER not in shift.world.stage.props


def test_a_ready_customer_waits_for_the_box_or_for_the_handoff_timeout():
    shift = Shift()
    shift.order("ORDER_RECEIVED", "RECEIVED")
    shift.watch(4)
    shift.order("ORDER_READY", "READY", version=2)  # no cooking events seen (a resync could cause this)
    customer = shift.world.customers[ORDER]
    waited = 0.0
    while not customer.carrying and waited < 20:
        shift.world.tick(0.05)
        waited += 0.05
    assert HANDOFF_WAIT_S <= waited < HANDOFF_WAIT_S + 6

    quick = Shift()
    quick.order("ORDER_RECEIVED", "RECEIVED")
    quick.cook("COOK_STARTED", "COOKING")
    quick.watch(3)
    quick.cook("COOK_COMPLETED", "COOKED", version=2)
    quick.order("ORDER_READY", "READY", version=2)
    cues, carried = quick.watch(HANDOFF_WAIT_S)
    assert ORDER in carried and cues.index("bell") < cues.index("take")


def test_a_failed_order_never_hands_over_a_box():
    shift = Shift()
    shift.order("ORDER_RECEIVED", "RECEIVED")
    shift.cook("COOK_STARTED", "COOKING")
    shift.watch(3)
    shift.cook("COOK_COMPLETED", "COOKED", version=2)
    shift.watch(10)
    assert ORDER in shift.world.stage.props
    shift.order("ORDER_FAILED", "FAILED", version=2, failure="FULFILLMENT_UNCONFIRMED")
    cues, carried = shift.watch(15)
    assert "bin" in cues and ORDER not in shift.world.stage.props
    assert not carried and ORDER not in shift.world.customers


def test_back_to_back_jobs_keep_the_chef_close_behind_the_kitchen():
    shift = Shift()
    ids = [f"00000000-0000-4000-8000-0000000000{n:02d}" for n in range(10)]
    finished = {}
    shift.cook("COOK_STARTED", "COOKING", ids[0])
    for index, order_id in enumerate(ids):  # one cook, a 10 s bake, the next job starts the moment one finishes
        shift.watch(10)
        shift.cook("COOK_COMPLETED", "COOKED", order_id, version=2)
        finished[order_id] = shift.world.time
        if index + 1 < len(ids):
            shift.cook("COOK_STARTED", "COOKING", ids[index + 1])
    shift.watch(15)
    lags = [shift.world.stage.tickets[order_id].dropped_at - finished[order_id] for order_id in ids]
    assert max(lags) <= 10, lags


def test_one_speaker_says_one_line_at_a_time_in_order_and_stale_chatter_is_dropped():
    shift = Shift()
    stage, cashier = shift.world.stage, shift.world.actors["cashier"]
    stage.say(cashier, "first line", shift.world.time)
    stage.say(cashier, "second line", shift.world.time)
    assert cashier.speech == "first line"
    advance(shift.world, 3.0)
    assert cashier.speech == "second line"
    stage.say(cashier, "small talk", shift.world.time, ttl=0.5)
    heard = set()
    for _ in range(200):
        shift.world.tick(0.05)
        heard.add(cashier.speech)
    assert "small talk" not in heard

    shift.order("ORDER_RECEIVED", "RECEIVED")
    customer = shift.world.customers[ORDER]
    assert customer.speech and cashier.speech is None  # the customer speaks first...
    advance(shift.world, 1.2)
    assert cashier.speech  # ...and the cashier answers a moment later


def test_a_caught_stale_sheet_becomes_a_standoff_at_the_stockroom_door():
    shift = Shift()
    payload = {"reason": "STALE_STOCK_CONTRADICTION", "before": mood(version=1), "after": mood("CALM", trust=35, version=2),
               "missing": {"PEPPERONI": 1}, "claimed": {"PEPPERONI": 13}, "commandId": None}
    shift.world.apply(event("kitchen", "RELATIONSHIP_CHANGED", 1, payload, ORDER))
    chef, worker = shift.world.actors["chef"], shift.world.actors["stock_worker"]
    said = set()
    cues = [name for name, _, _ in shift.world.stage.take_cues()]
    for _ in range(160):
        shift.world.tick(0.05)
        said |= {chef.speech, worker.speech}
        if chef.emote == "angry":
            assert chef.position == pytest.approx(shift.world.stage.standoff["chef"], abs=0.05)
    accusations = {dialogue.fill(text, missing="pepperoni", claimed=13) for text in dialogue.CHEF["RELATIONSHIP_CHANGED"]["*"]}
    assert cues == ["sting"]
    assert said & accusations
    assert said & set(dialogue.STOCK_WORKER["STANDOFF"])
