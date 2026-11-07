import asyncio
import io
import json
import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import TypeAdapter

from services.order.events import Event, LOGGER
from services.order.main import create_app
from tests.order.harness import Gate, restaurant

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def api(**overrides):
    world = restaurant(**overrides)
    application = create_app(world.order_settings, service_factory=lambda settings: world.service)
    world._inventory_lifespan = world.inventory_app.router.lifespan_context(world.inventory_app)
    await world._inventory_lifespan.__aenter__()
    await world.start_kitchen()
    try:
        async with application.router.lifespan_context(application):
            headers = {"X-Run-Id": str(world.order_settings.run_id), "X-Trace-Id": str(uuid4())}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://order",
                                         headers=headers) as client:
                yield client, world
    finally:
        await world.stop_kitchen()
        await world.admin.aclose()
        await world._inventory_lifespan.__aexit__(None, None, None)


def order(pizza="CHEESE", quantity=1, request_id=None, **customer):
    return {"requestId": str(request_id or uuid4()),
            "customer": {"id": str(uuid4()), "appearance": "customer_amber", "trait": "patient", **customer},
            "pizzaType": pizza, "quantity": quantity}


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"code", "message", "traceId", "details"}
    assert body["code"] == code
    assert str(UUID(body["traceId"])) == response.headers["X-Trace-Id"]
    assert UUID(response.headers["X-Run-Id"]) and UUID(response.headers["X-Instance-Id"])
    return body


async def wait_until(client, order_id, *statuses, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        response = await client.get(f"/orders/{order_id}")
        assert response.status_code == 200, response.text
        if response.json()["status"] in statuses:
            return response.json()
        assert asyncio.get_running_loop().time() < deadline, response.text
        await asyncio.sleep(0.005)


async def test_order_is_created_then_ready_then_replayed_with_its_original_trace():
    async with api() as (client, world):
        body = order("PEPPERONI", 2, trait="chatty", appearance="customer_lavender")
        created = await client.post("/orders", json=body)
        assert created.status_code == 201, created.text
        record = created.json()
        assert created.headers["Location"] == f"/orders/{record['orderId']}"
        assert record["requestId"] == body["requestId"] and record["status"] == "RECEIVED" and record["version"] == 1
        assert record["failureReason"] is None and record["lastError"] is None and record["traceId"] == client.headers["X-Trace-Id"]
        assert record["customer"] == body["customer"] and record["pizzaType"] == "PEPPERONI" and record["quantity"] == 2
        assert {"jobId", "kitchenStatus", "reservationId", "attempts", "sequence", "createdAt", "completedAt"} <= set(record)
        done = await wait_until(client, record["orderId"], "READY", "FAILED", "UNKNOWN")
        assert done["status"] == "READY" and done["kitchenStatus"] == "COOKED" and UUID(done["reservationId"])
        replay = await client.post("/orders", json=body, headers={"X-Trace-Id": str(uuid4())})
        assert replay.status_code == 200 and replay.json() == done
        assert replay.json()["traceId"] == client.headers["X-Trace-Id"]
        assert_error(await client.post("/orders", json=body | {"quantity": 3}), 409, "IDEMPOTENCY_CONFLICT")
        assert (await world.stock())["PEPPERONI"] == 13
        assert_error(await client.get(f"/orders/{uuid4()}"), 404, "ORDER_NOT_FOUND")
        assert_error(await client.get("/orders/not-a-uuid"), 400, "INVALID_REQUEST")


async def test_shortage_and_unknown_pizza():
    async with api(stock={"CHEESE": 0}) as (client, _):
        body = order("CHEESE", 1)
        assert (await client.post("/orders", json=body)).status_code == 201
        done = await wait_until(client, (await client.post("/orders", json=body)).json()["orderId"], "READY", "FAILED", "UNKNOWN")
        assert done["status"] == "FAILED" and done["failureReason"] == "INSUFFICIENT_STOCK"
        assert done["lastError"]["code"] == "INSUFFICIENT_STOCK" and done["lastError"]["details"]["jobError"]["status"] == 409
        assert_error(await client.post("/orders", json=order("HAWAIIAN")), 400, "UNKNOWN_PIZZA")


@pytest.mark.parametrize("body", [None, {}, order() | {"extra": 1}, order(quantity=0), order(quantity=-1), order(quantity=True),
                                  order(quantity=1.0), order(quantity="1"), order(quantity=21), order(pizza="cheese"),
                                  order(trait="angry"), order(appearance="customer_pink"), order(id="nope"),
                                  {k: v for k, v in order().items() if k != "customer"},
                                  {k: v for k, v in order().items() if k != "requestId"}, order(request_id="invalid")])
async def test_invalid_orders_are_normalized_and_create_nothing(body):
    async with api() as (client, world):
        assert_error(await client.post("/orders", json=body), 400, "INVALID_REQUEST")
        assert (await client.get("/snapshot")).json()["orders"] == [] and world.calls() == []


async def test_headers_run_checks_health_and_unknown_routes():
    async with api() as (client, world):
        assert_error(await client.post("/orders", content="{", headers={"Content-Type": "application/json"}), 400, "INVALID_REQUEST")
        assert_error(await client.get("/orders", headers={"X-Trace-Id": "invalid"}), 400, "INVALID_REQUEST")
        assert_error(await client.get("/orders", headers={"X-Run-Id": "invalid"}), 400, "INVALID_REQUEST")
        assert_error(await client.post("/orders", json=order(), headers={"X-Run-Id": str(uuid4())}), 409, "RUN_MISMATCH")
        del client.headers["X-Run-Id"]
        assert_error(await client.get("/orders"), 400, "INVALID_REQUEST")
        del client.headers["X-Trace-Id"]
        health = await client.get("/health")
        assert health.json() == {"status": "UP", "service": "order", "runId": str(world.order_settings.run_id),
                                 "instanceId": str(world.service.instance_id)}
        assert_error(await client.get("/missing"), 404, "NOT_FOUND")
        response = await client.delete("/snapshot")
        assert_error(response, 405, "METHOD_NOT_ALLOWED")
        assert "GET" in response.headers["Allow"]


async def test_order_generates_a_trace_when_none_is_given():
    async with api() as (client, _):
        del client.headers["X-Trace-Id"]
        created = await client.post("/orders", json=order())
        assert created.status_code == 201
        assert created.json()["traceId"] == created.headers["X-Trace-Id"] and UUID(created.headers["X-Trace-Id"])


async def test_listing_paginates_newest_first_over_http():
    async with api() as (client, _):
        ids = [(await client.post("/orders", json=order())).json()["orderId"] for _ in range(3)]
        page = await client.get("/orders", params={"limit": 2})
        assert page.status_code == 200
        assert [item["orderId"] for item in page.json()["orders"]] == [ids[2], ids[1]]
        rest = await client.get("/orders", params={"limit": 2, "cursor": page.json()["nextCursor"]})
        assert [item["orderId"] for item in rest.json()["orders"]] == [ids[0]] and rest.json()["nextCursor"] is None
        assert_error(await client.get("/orders", params={"limit": 0}), 400, "INVALID_REQUEST")
        assert_error(await client.get("/orders", params={"limit": 101}), 400, "INVALID_REQUEST")
        assert_error(await client.get("/orders", params={"cursor": "***"}), 400, "INVALID_CURSOR")


async def test_queue_full_returns_503_with_retry_after_and_no_order():
    gate = Gate()
    async with api(workers=1, queue_capacity=1, kitchen={"cooking_s": 1}, kitchen_sleep=gate.sleep) as (client, _):
        first = order()
        assert (await client.post("/orders", json=first)).status_code == 201
        await asyncio.wait_for(gate.entered.wait(), 2)
        assert (await client.post("/orders", json=order())).status_code == 201
        rejected = order()
        response = await client.post("/orders", json=rejected)
        assert_error(response, 503, "QUEUE_FULL")
        assert response.headers["Retry-After"] == "1"
        assert (await client.post("/orders", json=first)).status_code == 200
        assert len((await client.get("/orders")).json()["orders"]) == 2
        gate.release.set()
        assert (await wait_until(client, (await client.post("/orders", json=first)).json()["orderId"], "READY"))["status"] == "READY"


async def test_snapshot_events_logs_and_openapi(monkeypatch):
    logs = io.StringIO()
    monkeypatch.setattr(LOGGER, "handlers", [logging.StreamHandler(logs)])
    async with api() as (client, world):
        snapshot = (await client.get("/snapshot")).json()
        assert snapshot["service"] == "order" and snapshot["menu"] == ["CHEESE", "PEPPERONI", "MARINARA"]
        assert snapshot["queue"] == {"capacity": 50, "depth": 0, "workers": 4, "accepting": True}
        assert snapshot["kitchen"]["url"] == world.order_settings.kitchen_url
        watermark = snapshot["watermark"]
        created = (await client.post("/orders", json=order())).json()
        done = await wait_until(client, created["orderId"], "READY")
        page = (await client.get("/events", params={"instanceId": watermark["instanceId"], "after": watermark["sequence"]})).json()
        types = [event["type"] for event in page["events"]]
        assert types[0] == "ORDER_RECEIVED" and types[-1] == "ORDER_READY" and not page["hasMore"]
        assert all(event["orderId"] == created["orderId"] for event in page["events"])
        assert page["events"][-1]["payload"] == done
        assert all(TypeAdapter(Event).validate_python(event) for event in page["events"])
        logged = [json.loads(line) for line in logs.getvalue().splitlines()]
        assert logged[-len(page["events"]):] == page["events"]
        assert (await client.get("/snapshot")).json()["watermark"] == page["nextCursor"]
        assert_error(await client.get("/events", params={"instanceId": str(uuid4())}), 410, "EVENT_CURSOR_RESET")
        assert_error(await client.get("/events", params={"instanceId": watermark["instanceId"], "after": 999}), 400, "INVALID_CURSOR")
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/orders"]["post"]
        assert {"200", "201", "400", "409", "503"} <= set(operation["responses"]) and "422" not in operation["responses"]
        assert set(schema["components"]["schemas"]["OrderStatus"]["enum"]) == {
            "RECEIVED", "CHECKING", "COOKING", "RECONCILING", "READY", "FAILED", "UNKNOWN"}
