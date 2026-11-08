import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from services.order.main import create_app as create_order
from tests.order.harness import restaurant
from viewer.feed import Client, Feed, Poller, Replay
from viewer.updates import Event, Resync, Snapshot, Status, dumps, from_record
from viewer.world import World

pytestmark = pytest.mark.anyio


def order_body(pizza="CHEESE", quantity=1):
    return {"requestId": str(uuid4()), "pizzaType": pizza, "quantity": quantity,
            "customer": {"id": str(uuid4()), "appearance": "customer_teal", "trait": "chatty"}}


class Collecting:
    def __init__(self):
        self.items = []

    def __call__(self, update):
        self.items.append(update)

    def of(self, kind):
        return [item for item in self.items if isinstance(item, kind)]


async def live_restaurant():
    world = restaurant(kitchen={"cooking_s": 0})
    order_app = create_order(world.order_settings, service_factory=lambda settings: world.service)
    world._inventory_lifespan = world.inventory_app.router.lifespan_context(world.inventory_app)
    await world._inventory_lifespan.__aenter__()
    await world.start_kitchen()
    order_life = order_app.router.lifespan_context(order_app)
    await order_life.__aenter__()
    transports = {
        "inventory": httpx.ASGITransport(app=world.inventory_app),
        "kitchen": httpx.ASGITransport(app=world.kitchen_app),
        "order": httpx.ASGITransport(app=order_app),
    }
    urls = {name: f"http://{name}" for name in transports}
    return world, order_app, order_life, urls, transports


async def close_restaurant(world, order_life):
    await order_life.__aexit__(None, None, None)
    await world.stop_kitchen()
    await world.admin.aclose()
    await world._inventory_lifespan.__aexit__(None, None, None)


async def test_poller_snapshot_then_events_for_a_ready_order():
    world, order_app, life, urls, transports = await live_restaurant()
    collected = Collecting()
    try:
        poller = Poller(urls, world.order_settings.run_id, collected, transports=transports, poll_s=0.01)
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(0.05)
        assert {item.service for item in collected.of(Snapshot)} == {"order", "kitchen", "inventory"}
        async with httpx.AsyncClient(transport=transports["order"], base_url="http://order",
                                     headers={"X-Run-Id": str(world.order_settings.run_id)}, timeout=2) as client:
            created = await client.post("/orders", json=order_body("PEPPERONI", 1))
            assert created.status_code == 201, created.text
            order_id = created.json()["orderId"]
            for _ in range(80):
                if any(isinstance(item, Event) and item.data.get("type") == "ORDER_READY" for item in collected.items):
                    break
                await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        restaurant_world = World()
        for update in collected.items:
            restaurant_world.apply(update)
        assert restaurant_world.orders[order_id]["status"] == "READY"
        assert restaurant_world.stock["PEPPERONI"] == 14
        types = [item.data["type"] for item in collected.of(Event) if item.data.get("orderId") == order_id]
        assert "ORDER_RECEIVED" in types and "ORDER_READY" in types
        assert "COOK_STARTED" in types and "RESERVATION_SUCCEEDED" in types
    finally:
        await close_restaurant(world, life)


async def test_stale_cursor_forces_a_new_snapshot_instead_of_inventing_events():
    world, order_app, life, urls, transports = await live_restaurant()
    collected = Collecting()
    try:
        poller = Poller(urls, world.order_settings.run_id, collected, transports=transports, poll_s=0.01)
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(0.04)
        poller.cursors["order"].instance_id = str(uuid4())
        poller.cursors["order"].sequence = 99
        poller.cursors["order"].next_at = 0
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        snapshots = collected.of(Snapshot)
        assert len([item for item in snapshots if item.service == "order"]) >= 2
    finally:
        await close_restaurant(world, life)


async def test_wrong_run_id_is_a_status_not_a_hang():
    world, order_app, life, urls, transports = await live_restaurant()
    collected = Collecting()
    try:
        poller = Poller(urls, uuid4(), collected, transports=transports, poll_s=0.01)
        task = asyncio.create_task(poller.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert {item.state for item in collected.of(Status)} >= {"RUN_MISMATCH"}
        assert collected.of(Snapshot) == []
    finally:
        await close_restaurant(world, life)


def test_full_queue_signals_resync_instead_of_dropping_quietly():
    feed = Feed({"order": "http://x", "kitchen": "http://x", "inventory": "http://x"}, uuid4(), queue_size=2)
    for index in range(5):
        feed._emit(Status("order", "LIVE", str(index), 1.0))
    drained = feed.drain()
    assert any(isinstance(item, Resync) for item in drained)


def test_recording_round_trips_through_replay(tmp_path):
    path = tmp_path / "shift.jsonl"
    updates = [Status("order", "LIVE", None, 10.0),
               Snapshot("order", {"service": "order", "runId": "r", "instanceId": "i",
                                  "orders": [], "menu": ["CHEESE"], "queue": {}, "kitchen": {},
                                  "watermark": {"instanceId": "i", "sequence": 0}}, 10.1),
               Event("order", {"type": "ORDER_RECEIVED", "sequence": 1, "instanceId": "i"}, 10.4)]
    path.write_text("".join(dumps(item) + "\n" for item in updates))
    clock = {"t": 0.0}
    replay = Replay(path, now=lambda: clock["t"])
    first = replay.drain()
    assert len(first) == 1 and isinstance(first[0], Status)
    clock["t"] = 0.5
    rest = replay.drain()
    assert [type(item) for item in rest] == [Snapshot, Event]
    assert replay.finished


def test_record_helpers_reject_unknown_kinds():
    payload = {"kind": "event", "service": "order", "data": {"type": "ORDER_READY"}, "receivedAt": 1.0}
    assert from_record(payload).data["type"] == "ORDER_READY"
    with pytest.raises(ValueError):
        from_record({"kind": "nope", "receivedAt": 1})
