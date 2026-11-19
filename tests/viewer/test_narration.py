import json
import re

from tests.viewer.harness import event, job_record, mood, order_record
from viewer.assets import DEFAULT_ASSET_ROOT
from viewer.narration import Story, narrate, status_story
from viewer.updates import Status
from viewer.world import World

ORDER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
SPRITES = set(json.loads((DEFAULT_ASSET_ROOT / "manifest.json").read_text())["sprites"])
CLEAR = {"staleStockEnabled": False, "delayMs": 0, "failureMode": "NONE", "delayPhase": "BEFORE_MUTATION"}
FAILED = order_record(ORDER, status="FAILED", failure="INSUFFICIENT_STOCK", pizza="PEPPERONI") | {
    "lastError": {"details": {"jobError": {"details": {"missing": {"PEPPERONI": 2}}}}}}

CATALOG = {
    "order": {
        "ORDER_RECEIVED": order_record(ORDER), "ORDER_CHECKING": order_record(ORDER, status="CHECKING"),
        "ORDER_COOKING": order_record(ORDER, status="COOKING"), "ORDER_RECONCILING": order_record(ORDER, status="RECONCILING"),
        "ORDER_READY": order_record(ORDER, status="READY", quantity=2), "ORDER_FAILED": FAILED,
        "ORDER_UNKNOWN": order_record(ORDER, status="UNKNOWN", failure="FULFILLMENT_UNCONFIRMED"),
        "DEPENDENCY_TIMEOUT": {"operation": "lookup", "attempt": 1, "code": "TIMEOUT", "message": "x", "status": None},
        "DEPENDENCY_UNAVAILABLE": {"operation": "submit", "attempt": 1, "code": "CONNECTION_FAILED", "message": "x", "status": None},
        "SERVICE_STARTED": {"status": "UP"}, "SERVICE_STOPPING": {"status": "STOPPING"},
    },
    "kitchen": {
        "COOK_QUEUED": job_record(ORDER), "COOK_CHECKING": job_record(ORDER, status="CHECKING"),
        "STOCK_CHECKED": {"authoritative": True, "source": "AUTHORITATIVE", "sufficient": False, "needed": {"CHEESE": 1},
                          "available": {"CHEESE": 0}, "missing": {"CHEESE": 1}, "stockVersion": 4, "snapshotVersion": 4},
        "COOK_STARTED": job_record(ORDER, status="COOKING"), "COOK_COMPLETED": job_record(ORDER, status="COOKED"),
        "COOK_FAILED": job_record(ORDER, status="FAILED", failure="INSUFFICIENT_STOCK"),
        "COOK_RECONCILING": job_record(ORDER, status="RECONCILING"), "COOK_UNKNOWN": job_record(ORDER, status="UNKNOWN"),
        "RELATIONSHIP_CHANGED": {"reason": "STALE_STOCK_CONTRADICTION", "before": mood(), "after": mood("SUSPICIOUS", trust=35, version=2),
                                 "missing": {"PEPPERONI": 1}, "claimed": {"PEPPERONI": 13}, "commandId": None},
        "MOOD_CHANGED": {"reason": "STALE_STOCK_CONTRADICTION", "before": mood(), "after": mood("TENSE", patience=50, version=2), "commandId": None},
        "STALE_STOCK_CONTRADICTION": {"missing": {"PEPPERONI": 1}},
        "DEPENDENCY_TIMEOUT": {"operation": "reserve", "attempt": 1, "code": "TIMEOUT", "message": "x", "status": None},
        "DEPENDENCY_UNAVAILABLE": {"operation": "stock", "attempt": 2, "code": "INVENTORY_UNAVAILABLE", "message": "x", "status": 503},
        "SERVICE_STARTED": {"status": "UP"}, "SERVICE_STOPPING": {"status": "STOPPING"},
    },
    "inventory": {
        "STOCK_READ": {"items": {"CHEESE": 3}, "source": "CACHED", "stockVersion": 2, "snapshotVersion": 1},
        "RESERVATION_SUCCEEDED": {"reservationId": ORDER, "orderId": ORDER, "status": "RESERVED", "items": {"CHEESE": 1},
                                  "stockVersion": 3, "traceId": ORDER},
        "RESERVATION_REJECTED": {"code": "INSUFFICIENT_STOCK", "items": {"CHEESE": 2}, "stockVersion": 3, "missing": {"CHEESE": 1}},
        "STOCK_RESTOCKED": {"commandId": ORDER, "added": {"PEPPERONI": 15, "CHEESE": 8}, "items": {"PEPPERONI": 15}, "stockVersion": 4},
        "FAULT_CONFIG_CHANGED": {"commandId": ORDER, "faults": CLEAR | {"staleStockEnabled": True}, "snapshotVersion": 4},
        "SERVICE_STARTED": {"status": "UP"}, "SERVICE_STOPPING": {"status": "STOPPING"},
    },
}


def test_every_catalog_event_reads_as_a_plain_sentence():
    world = World()
    world.orders[ORDER] = order_record(ORDER)
    told = 0
    for service, kinds in CATALOG.items():
        for sequence, (kind, payload) in enumerate(kinds.items(), start=1):
            story = narrate(service, event(service, kind, sequence, payload, ORDER).data, world)
            if story is None:
                continue
            told += 1
            assert isinstance(story, Story) and story.text and story.tone in ("good", "bad", "warn", "info")
            assert story.icon in SPRITES, story
            assert not re.search(r"[0-9a-f]{8}", story.text), story.text  # no IDs
            assert not re.search(r"\b[A-Z]+_[A-Z_]+\b", story.text), story.text  # no codes or event names
    assert told >= 15


def test_failures_say_what_ran_out_and_changes_are_described_against_the_old_state():
    world = World()
    failed = narrate("order", event("order", "ORDER_FAILED", 1, FAILED, ORDER).data, world)
    assert failed.tone == "bad" and "pepperoni" in failed.text.lower()
    closed = order_record(ORDER, status="FAILED", failure="INVENTORY_UNAVAILABLE")
    assert "stockroom" in narrate("order", event("order", "ORDER_FAILED", 2, closed, ORDER).data, world).text
    world.faults = CLEAR | {"staleStockEnabled": True}
    outage = {"commandId": ORDER, "faults": CLEAR | {"staleStockEnabled": True, "failureMode": "UNAVAILABLE"}, "snapshotVersion": 4}
    assert "closed" in narrate("inventory", event("inventory", "FAULT_CONFIG_CHANGED", 3, outage).data, world).text
    world.faults = outage["faults"]
    reopened = {"commandId": ORDER, "faults": CLEAR | {"staleStockEnabled": True}, "snapshotVersion": 4}
    assert "opened again" in narrate("inventory", event("inventory", "FAULT_CONFIG_CHANGED", 4, reopened).data, world).text


def test_the_world_tells_a_repeated_story_once_and_reports_lost_contact():
    world = World()
    timeout = CATALOG["kitchen"]["DEPENDENCY_TIMEOUT"]
    world.apply(event("kitchen", "DEPENDENCY_TIMEOUT", 1, timeout, ORDER))
    world.apply(event("kitchen", "DEPENDENCY_TIMEOUT", 2, timeout, ORDER))
    assert [story.text for _, story in world.story].count("The kitchen gave up waiting on the stockroom") == 1
    world.apply(Status("kitchen", "LIVE", None, 1.0))
    world.apply(Status("kitchen", "DISCONNECTED", "refused", 2.0))
    assert world.story[-1][1].text == "Lost contact with the kitchen"
    assert status_story("kitchen", "LIVE", "DISCONNECTED").tone == "good"
