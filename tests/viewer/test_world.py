from uuid import uuid4

import pytest

from tests.viewer.harness import advance, event, job_record, mood, order_record, snapshot
from viewer.updates import Resync, Status
from viewer.world import World


ORDER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def received(world, **fields):
    record = order_record(ORDER, **fields)
    world.apply(event("order", "ORDER_RECEIVED", 1, record, ORDER, entity_version=1))
    return record


def test_newer_order_versions_win_and_older_ones_are_ignored():
    world = World()
    first = order_record(ORDER, status="RECEIVED", version=1)
    later = {**first, "status": "READY", "version": 3}
    older = {**first, "status": "FAILED", "version": 2, "failureReason": "INSUFFICIENT_STOCK"}
    world.apply(event("order", "ORDER_RECEIVED", 1, first, ORDER, entity_version=1))
    world.apply(event("order", "ORDER_READY", 2, later, ORDER, entity_version=3))
    world.apply(event("order", "ORDER_FAILED", 3, older, ORDER, entity_version=2))
    assert world.orders[ORDER]["status"] == "READY" and world.orders[ORDER]["version"] == 3


def test_duplicate_events_are_applied_once():
    world = World()
    record = order_record(ORDER)
    first = event("order", "ORDER_RECEIVED", 4, record, ORDER)
    assert world.apply(first) is True
    assert world.apply(first) is False
    assert world.apply(event("order", "ORDER_RECEIVED", 4, record, ORDER)) is False
    assert len(world.customers) == 1


def test_ready_customer_collects_a_box_failed_customer_leaves_empty_handed():
    world = World()
    received(world, appearance="customer_teal", trait="impatient")
    customer = world.customers[ORDER]
    world.apply(event("order", "ORDER_READY", 2, order_record(ORDER, status="READY", version=2, appearance="customer_teal"), ORDER))
    # READY, but no box has reached the counter yet: the customer waits rather than leaving with an invisible box.
    assert customer.state == "collecting" and customer.carrying is None
    carried = False
    for _ in range(600):
        world.tick(0.05)
        carried = carried or customer.carrying == "pizza_box"
    assert carried and ORDER not in world.customers

    other = str(uuid4())
    record = order_record(other, status="RECEIVED", appearance="customer_lavender")
    world.apply(event("order", "ORDER_RECEIVED", 3, record, other))
    world.apply(event("order", "ORDER_FAILED", 4, {**record, "status": "FAILED", "version": 2, "failureReason": "INSUFFICIENT_STOCK"}, other))
    leaving = world.customers[other]
    assert leaving.state == "leaving" and leaving.carrying is None
    world.tick(0.05)
    assert leaving.action == "frustrated"
    advance(world, 20)
    assert other not in world.customers and leaving.carrying is None


def test_snapshot_from_a_new_instance_replaces_that_service_and_leaves_the_others():
    world = World()
    received(world)
    world.apply(event("kitchen", "COOK_QUEUED", 1, job_record(ORDER), ORDER))
    world.apply(snapshot("order", 0, orders=[], menu=["CHEESE"], instanceId="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"))
    assert world.orders == {}
    assert ORDER in world.jobs
    assert world.services["order"].instance_id.endswith("eeee")


def test_stock_tracks_consecutive_reservations_and_marks_gaps_uncertain():
    world = World()
    world.apply(snapshot("inventory", 0, items={"CHEESE": 30, "DOUGH": 40}, stockVersion=1, reservations=[],
                         faults={"staleStockEnabled": False, "delayMs": 0, "delayPhase": "BEFORE_MUTATION",
                                 "failureMode": "NONE", "failAfter": 0}))
    world.apply(event("inventory", "RESERVATION_SUCCEEDED", 1,
                      {"reservationId": str(uuid4()), "orderId": ORDER, "status": "RESERVED",
                       "items": {"CHEESE": 2, "DOUGH": 2}, "stockVersion": 2, "traceId": str(uuid4())}, ORDER))
    assert world.stock["CHEESE"] == 28 and world.stock_version == 2 and not world.stock_uncertain
    world.apply(event("inventory", "RESERVATION_SUCCEEDED", 2,
                      {"reservationId": str(uuid4()), "orderId": str(uuid4()), "status": "RESERVED",
                       "items": {"CHEESE": 1}, "stockVersion": 5, "traceId": str(uuid4())}))
    assert world.stock_uncertain and world.stock["CHEESE"] == 28


def test_chef_preps_bakes_and_sets_the_box_on_the_counter():
    world = World()
    chef = world.actors["chef"]
    home = chef.position
    world.apply(event("kitchen", "COOK_STARTED", 1, job_record(ORDER, status="COOKING", pizza="PEPPERONI"), ORDER))
    advance(world, 0.1)
    assert chef.action == "work"  # shaping dough at the prep counter first
    advance(world, 3)
    assert chef.position == pytest.approx(world.oven_spot, abs=0.05) and chef.action == "work"
    world.apply(event("kitchen", "COOK_COMPLETED", 2, job_record(ORDER, status="COOKED", version=2, pizza="PEPPERONI"), ORDER))
    advance(world, 1.0)
    assert chef.carrying == "pizza_box"
    advance(world, 20)  # the pass is reached through the kitchen doorway
    assert chef.carrying is None
    assert ORDER in world.stage.props  # the box waits on the counter for its customer
    assert chef.position == pytest.approx(home, abs=0.05)


def test_mood_moves_forward_only_and_a_resync_is_noted():
    world = World()
    world.apply(event("kitchen", "MOOD_CHANGED", 1, {"reason": "RESERVATION_SUCCEEDED", "before": mood(version=1),
                                                     "after": mood("CALM", trust=65, version=2), "commandId": None}))
    world.apply(event("kitchen", "MOOD_CHANGED", 2, {"reason": "RESERVATION_SUCCEEDED", "before": mood(version=1),
                                                     "after": mood("CALM", trust=50, version=1), "commandId": None}))
    assert world.mood["trust"] == 65
    world.apply(Resync(1.0))
    assert world.resyncs == 1 and world.ticker[-1]["type"] == "RESYNC"


def test_waiting_customers_take_distinct_slots():
    world = World()
    for index in range(3):
        order_id = f"00000000-0000-4000-8000-00000000000{index}"
        world.apply(event("order", "ORDER_RECEIVED", index + 1, order_record(order_id, sequence=index + 1), order_id))
    slots = {customer.slot for customer in world.customers.values()}
    assert len(slots) == 3


def test_connection_status_is_recorded_per_service():
    world = World()
    world.apply(Status("kitchen", "DISCONNECTED", "connection refused", 1.0))
    assert world.services["kitchen"].state == "DISCONNECTED"
    world.apply(Status("kitchen", "LIVE", None, 2.0))
    assert world.services["kitchen"].state == "LIVE"
