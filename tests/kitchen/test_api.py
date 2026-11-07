import asyncio
import io
import json
import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import TypeAdapter

from services.kitchen.events import Event, LOGGER
from services.kitchen.main import create_app
from tests.kitchen.harness import Gate, restaurant

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def api(**overrides):
    world = restaurant(**overrides)
    application = create_app(world.kitchen_settings, kitchen_factory=lambda settings: world.kitchen)
    await world.start_inventory()
    try:
        async with application.router.lifespan_context(application):
            headers = {"X-Run-Id": str(world.kitchen_settings.run_id), "X-Trace-Id": str(uuid4())}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://kitchen",
                                         headers=headers) as client:
                yield client, world
    finally:
        await world.admin.aclose()
        await world.stop_inventory()


def cook(pizza="CHEESE", quantity=1, order_id=None):
    return {"orderId": str(order_id or uuid4()), "pizzaType": pizza, "quantity": quantity}


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
        response = await client.get(f"/cook/{order_id}")
        assert response.status_code == 200, response.text
        if response.json()["status"] in statuses:
            return response.json()
        assert asyncio.get_running_loop().time() < deadline, response.text
        await asyncio.sleep(0.005)


async def test_cook_is_accepted_then_observable_then_replayed():
    async with api() as (client, world):
        body = cook("PEPPERONI", 2)
        accepted = await client.post("/cook", json=body)
        assert accepted.status_code == 202, accepted.text
        assert accepted.headers["Location"] == f"/cook/{body['orderId']}"
        job = accepted.json()
        assert job["orderId"] == body["orderId"] and job["status"] == "QUEUED" and job["version"] == 1
        assert job["result"] is None and job["failureReason"] is None and job["reservationId"] is None
        assert job["traceId"] == client.headers["X-Trace-Id"] and UUID(job["jobId"])
        assert {"deadlineAt", "lastError", "startedAt", "completedAt", "attempts", "items"} <= set(job)
        done = await wait_until(client, body["orderId"], "COOKED", "FAILED", "UNKNOWN")
        assert done["status"] == "COOKED" and done["result"] == "COOKED"
        assert UUID(done["reservationId"]) and done["startedAt"] and done["completedAt"]
        replay = await client.post("/cook", json=body, headers={"X-Trace-Id": str(uuid4())})
        assert replay.status_code == 200 and replay.json() == done
        assert_error(await client.post("/cook", json=body | {"quantity": 3}), 409, "IDEMPOTENCY_CONFLICT")
        stock = await world.stock()
        assert stock["PEPPERONI"] == 13 and stock["CHEESE"] == 28
        assert_error(await client.get(f"/cook/{uuid4()}"), 404, "COOK_JOB_NOT_FOUND")
        assert_error(await client.get("/cook/not-a-uuid"), 400, "INVALID_REQUEST")


async def test_shortage_is_a_known_failure_with_http_200_on_lookup():
    async with api(stock={"CHEESE": 0}) as (client, world):
        body = cook("CHEESE", 1)
        assert (await client.post("/cook", json=body)).status_code == 202
        done = await wait_until(client, body["orderId"], "COOKED", "FAILED", "UNKNOWN")
        assert done["status"] == "FAILED" and done["failureReason"] == "INSUFFICIENT_STOCK"
        assert done["lastError"]["code"] == "INSUFFICIENT_STOCK" and done["lastError"]["status"] == 409
        assert done["lastError"]["details"]["missing"] == {"CHEESE": 1}
        assert done["lastError"]["details"]["operation"] == "reserve"
        marinara = cook("MARINARA", 1)
        assert (await client.post("/cook", json=marinara)).status_code == 202
        assert (await wait_until(client, marinara["orderId"], "COOKED", "FAILED", "UNKNOWN"))["status"] == "COOKED"
        assert (await client.get("/mood")).json()["failedReservationCount"] == 1


@pytest.mark.parametrize("body", [None, {}, {"pizzaType": "CHEESE", "quantity": 1}, cook() | {"extra": 1},
                                  cook() | {"orderId": "invalid"}, cook(pizza="cheese"), cook(pizza=""), cook(quantity=0),
                                  cook(quantity=-1), cook(quantity=True), cook(quantity=1.0), cook(quantity="1"),
                                  cook(quantity=None), cook(quantity=21)])
async def test_invalid_cook_requests_are_normalized_and_create_nothing(body):
    async with api() as (client, world):
        assert_error(await client.post("/cook", json=body), 400, "INVALID_REQUEST")
        assert (await client.get("/snapshot")).json()["jobs"] == [] and world.calls() == []


async def test_unknown_pizza_headers_and_run_checks():
    async with api() as (client, world):
        assert_error(await client.post("/cook", json=cook(pizza="HAWAIIAN")), 400, "UNKNOWN_PIZZA")
        assert_error(await client.post("/cook", content="{", headers={"Content-Type": "application/json"}), 400, "INVALID_REQUEST")
        assert_error(await client.get("/mood", headers={"X-Trace-Id": "invalid"}), 400, "INVALID_REQUEST")
        assert_error(await client.get("/mood", headers={"X-Run-Id": "invalid"}), 400, "INVALID_REQUEST")
        assert_error(await client.post("/cook", json=cook(), headers={"X-Run-Id": str(uuid4())}), 409, "RUN_MISMATCH")
        del client.headers["X-Run-Id"]
        assert_error(await client.get("/mood"), 400, "INVALID_REQUEST")
        del client.headers["X-Trace-Id"]
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "UP", "service": "kitchen", "runId": str(world.kitchen_settings.run_id),
                                 "instanceId": str(world.kitchen.instance_id)}
        assert_error(await client.get("/missing"), 404, "NOT_FOUND")
        response = await client.delete("/mood")
        assert_error(response, 405, "METHOD_NOT_ALLOWED")
        assert "GET" in response.headers["Allow"]


async def test_queue_full_returns_503_with_retry_after_and_no_job():
    gate = Gate()
    async with api(workers=1, queue_capacity=1, cooking_s=1, sleep=gate.sleep) as (client, world):
        first = cook()
        assert (await client.post("/cook", json=first)).status_code == 202
        await asyncio.wait_for(gate.entered.wait(), 2)
        assert (await client.post("/cook", json=cook())).status_code == 202
        rejected = cook()
        response = await client.post("/cook", json=rejected)
        assert_error(response, 503, "QUEUE_FULL")
        assert response.headers["Retry-After"] == "1"
        assert_error(await client.get(f"/cook/{rejected['orderId']}"), 404, "COOK_JOB_NOT_FOUND")
        assert (await client.post("/cook", json=first)).status_code == 200
        gate.release.set()
        assert (await wait_until(client, first["orderId"], "COOKED"))["status"] == "COOKED"


async def test_mood_reconcile_snapshot_events_and_logs(monkeypatch):
    logs = io.StringIO()
    monkeypatch.setattr(LOGGER, "handlers", [logging.StreamHandler(logs)])
    async with api() as (client, world):
        mood = await client.get("/mood")
        assert mood.status_code == 200
        assert mood.json() == {"patience": 85, "stress": 0, "trust": 60, "failedReservationCount": 0, "mood": "CALM", "version": 1}
        snapshot = (await client.get("/snapshot")).json()
        assert snapshot["service"] == "kitchen" and snapshot["instanceId"] == str(world.kitchen.instance_id)
        assert snapshot["queue"] == {"capacity": 20, "depth": 0, "workers": 2, "accepting": True}
        assert snapshot["inventory"]["url"] == world.kitchen_settings.inventory_url
        watermark = snapshot["watermark"]
        command = {"commandId": str(uuid4())}
        reconciled = await client.post("/admin/reconcile", json=command)
        assert reconciled.status_code == 200, reconciled.text
        assert reconciled.json()["before"] == mood.json()
        assert reconciled.json()["mood"]["patience"] == 100 and reconciled.json()["mood"]["trust"] == 70
        assert (await client.post("/admin/reconcile", json=command)).json() == reconciled.json()
        assert_error(await client.post("/admin/reconcile", json={}), 400, "INVALID_REQUEST")
        assert (await client.get("/mood")).json()["version"] == 2
        page = (await client.get("/events", params={"instanceId": watermark["instanceId"], "after": watermark["sequence"]})).json()
        assert len(page["events"]) == 1 and not page["hasMore"]
        event = page["events"][0]
        assert event["type"] == "MOOD_CHANGED" and event["payload"]["reason"] == "RECONCILE"
        assert event["payload"]["commandId"] == command["commandId"] and event["orderId"] is None
        assert TypeAdapter(Event).validate_python(event).type == "MOOD_CHANGED"
        assert json.loads(logs.getvalue().strip().splitlines()[-1]) == event
        assert (await client.get("/snapshot")).json()["watermark"] == page["nextCursor"]
        assert_error(await client.get("/events", params={"instanceId": str(uuid4())}), 410, "EVENT_CURSOR_RESET")
        assert_error(await client.get("/events", params={"instanceId": watermark["instanceId"], "after": 999}), 400, "INVALID_CURSOR")
        assert_error(await client.get("/events", params={"instanceId": watermark["instanceId"], "limit": 501}), 400, "INVALID_REQUEST")
        assert await world.stock() == {"DOUGH": 40, "SAUCE": 40, "CHEESE": 30, "PEPPERONI": 15, "BASIL": 15}


async def test_openapi_documents_accepted_and_replayed_cook_responses():
    async with api() as (client, _):
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/cook"]["post"]
        assert {"200", "202", "400", "409", "503"} <= set(operation["responses"]) and "422" not in operation["responses"]
        headers = {entry["name"]: entry for entry in operation["parameters"]}
        assert headers["X-Run-Id"]["required"] and not headers["X-Trace-Id"]["required"]
        assert schema["components"]["schemas"]["EventPage"]["properties"]["events"]["items"]["discriminator"]["propertyName"] == "type"
        assert set(schema["components"]["schemas"]["JobStatus"]["enum"]) == {
            "QUEUED", "CHECKING", "COOKING", "COOKED", "FAILED", "RECONCILING", "UNKNOWN"}
