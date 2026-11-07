import asyncio
import io
import json
import logging
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from services.inventory.events import Event, LOGGER
from services.inventory.main import create_app
from services.inventory.settings import DEFAULT_STOCK, Settings


@pytest.fixture
def client():
    settings = Settings()
    with TestClient(create_app(settings)) as api:
        api.headers["X-Run-Id"] = str(settings.run_id)
        api.headers["X-Trace-Id"] = str(uuid4())
        yield api


def reservation(items=None, order_id=None):
    return {"orderId": str(order_id or uuid4()), "items": items if items is not None else {"CHEESE": 1}}


def fault(**values):
    return {"commandId": str(uuid4()), "staleStockEnabled": False, "delayMs": 0,
            "failureMode": "NONE", "delayPhase": "BEFORE_MUTATION", **values}


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"code", "message", "traceId", "details"}
    assert body["code"] == code
    assert str(UUID(body["traceId"])) == response.headers["X-Trace-Id"]
    assert UUID(response.headers["X-Run-Id"])
    assert UUID(response.headers["X-Instance-Id"])
    return body


def test_health_is_independent_and_instances_restart_without_shared_state():
    settings = Settings()
    application = create_app(settings)
    with TestClient(application) as client:
        first = client.get("/health")
        assert first.status_code == 200
        assert first.json()["service"] == "inventory"
        assert first.json()["runId"] == str(settings.run_id)
        client.post("/reservations", json=reservation(), headers={"X-Run-Id": str(settings.run_id)})
    with TestClient(application) as client:
        second = client.get("/health")
        assert first.json()["instanceId"] != second.json()["instanceId"]
        assert client.get("/stock", headers={"X-Run-Id": str(settings.run_id)}).json()["items"] == DEFAULT_STOCK


def test_stock_reservation_replay_lookup_and_trace(client):
    initial = client.get("/stock")
    assert initial.status_code == 200
    assert initial.json() == {"items": DEFAULT_STOCK, "source": "AUTHORITATIVE", "stockVersion": 1, "snapshotVersion": 1}
    body = reservation({"CHEESE": 2, "DOUGH": 2})
    original = client.post("/reservations", json=body)
    assert original.status_code == 200
    assert original.json()["traceId"] == client.headers["X-Trace-Id"]
    assert original.json()["stockVersion"] == 2
    replay = client.post("/reservations", json=body, headers={"X-Trace-Id": str(uuid4())})
    assert replay.json() == original.json()
    assert client.get(f"/reservations/{body['orderId']}").json() == original.json()
    assert client.get("/stock?ingredient=CHEESE").json()["items"] == {"CHEESE": 28}
    assert_error(client.post("/reservations", json=reservation({"CHEESE": 3}, body["orderId"])), 409, "IDEMPOTENCY_CONFLICT")


@pytest.mark.parametrize("quantity", [0, -1, True, False, 1.0, "1", None, 10_001])
def test_invalid_quantities_never_mutate(client, quantity):
    response = client.post("/reservations", json=reservation({"CHEESE": quantity}))
    assert_error(response, 400, "INVALID_REQUEST")
    assert client.get("/snapshot").json()["items"] == DEFAULT_STOCK


@pytest.mark.parametrize("body", [None, {}, {"items": {"CHEESE": 1}}, reservation({}),
                                   reservation() | {"extra": "no"}, reservation({"cheese": 1}),
                                   reservation() | {"orderId": "invalid"}])
def test_invalid_bodies_are_normalized(client, body):
    assert_error(client.post("/reservations", json=body), 400, "INVALID_REQUEST")


def test_malformed_json_and_headers_have_common_error_shape(client):
    assert_error(client.post("/reservations", content="{", headers={"Content-Type": "application/json"}), 400, "INVALID_REQUEST")
    assert_error(client.get("/stock", headers={"X-Trace-Id": "invalid"}), 400, "INVALID_REQUEST")
    assert_error(client.get("/stock", headers={"X-Run-Id": "invalid"}), 400, "INVALID_REQUEST")
    assert_error(client.post("/reservations", json=reservation(), headers={"X-Run-Id": str(uuid4())}), 409, "RUN_MISMATCH")
    del client.headers["X-Run-Id"]
    assert_error(client.get("/stock"), 400, "INVALID_REQUEST")
    del client.headers["X-Trace-Id"]
    assert client.get("/health").status_code == 200


def test_unknown_ingredients_and_shortages_are_atomic(client):
    assert_error(client.post("/reservations", json=reservation({"DOUGH": 2, "MYSTERY": 1})), 404, "UNKNOWN_INGREDIENT")
    shortage = assert_error(client.post("/reservations", json=reservation({"DOUGH": 2, "CHEESE": 31})), 409, "INSUFFICIENT_STOCK")
    assert shortage["details"]["missing"] == {"CHEESE": 1}
    assert shortage["details"]["stockVersion"] == 1
    assert client.get("/snapshot").json()["items"] == DEFAULT_STOCK
    assert_error(client.get("/stock?ingredient=MYSTERY"), 404, "UNKNOWN_INGREDIENT")
    assert_error(client.get(f"/reservations/{uuid4()}"), 404, "RESERVATION_NOT_FOUND")
    assert_error(client.get("/reservations/not-a-uuid"), 400, "INVALID_REQUEST")


def test_restock_replay_conflicts_and_unknown_item(client):
    command = {"commandId": str(uuid4()), "items": {"CHEESE": 5, "DOUGH": 1}}
    response = client.post("/admin/restock", json=command)
    assert response.status_code == 200
    assert client.post("/admin/restock", json=command).json() == response.json()
    assert client.get("/stock").json()["items"]["CHEESE"] == 35
    assert_error(client.post("/admin/restock", json=command | {"items": {"CHEESE": 6}}), 409, "IDEMPOTENCY_CONFLICT")
    assert_error(client.post("/admin/restock", json={"commandId": str(uuid4()), "items": {"CHEESE": 1, "MYSTERY": 1}}), 404, "UNKNOWN_INGREDIENT")
    assert client.get("/stock").json()["items"]["CHEESE"] == 35


def test_faults_stale_read_authoritative_read_and_recovery(client):
    command = fault(staleStockEnabled=True)
    response = client.post("/admin/faults", json=command)
    assert response.status_code == 200
    client.post("/reservations", json=reservation({"CHEESE": 30}))
    assert client.post("/admin/faults", json=command).json() == response.json()
    assert client.get("/stock?ingredient=CHEESE").json()["items"] == {"CHEESE": 30}
    assert client.get("/stock?ingredient=CHEESE&authoritative=true").json()["items"] == {"CHEESE": 0}
    assert_error(client.post("/reservations", json=reservation()), 409, "INSUFFICIENT_STOCK")
    client.post("/admin/faults", json=fault(failureMode="UNAVAILABLE"))
    assert_error(client.get("/stock"), 503, "INVENTORY_UNAVAILABLE")
    assert_error(client.post("/reservations", json=reservation()), 503, "INVENTORY_UNAVAILABLE")
    assert client.get("/health").status_code == 200
    snapshot = client.get("/snapshot")
    assert snapshot.status_code == 200
    assert client.get("/events", params={"instanceId": snapshot.json()["instanceId"], "after": 0}).status_code == 200
    assert client.post("/admin/restock", json={"commandId": str(uuid4()), "items": {"CHEESE": 2}}).status_code == 200
    assert client.post("/admin/faults", json=fault()).status_code == 200
    assert client.post("/reservations", json=reservation()).status_code == 200


@pytest.mark.parametrize("override", [{"delayMs": -1}, {"delayMs": 10_001}, {"delayMs": True},
                                      {"staleStockEnabled": "false"}, {"failureMode": "BOGUS"},
                                      {"delayPhase": "BOGUS"}, {"extra": 1}])
def test_fault_config_validation(client, override):
    assert_error(client.post("/admin/faults", json=fault(**override)), 400, "INVALID_REQUEST")
    assert client.get("/snapshot").json()["faultVersion"] == 0


def test_events_match_json_logs_and_snapshot_resumption(client, monkeypatch):
    logs = io.StringIO()
    monkeypatch.setattr(LOGGER, "handlers", [logging.StreamHandler(logs)])
    watermark = client.get("/snapshot").json()["watermark"]
    body = reservation()
    client.post("/reservations", json=body)
    page = client.get("/events", params={"instanceId": watermark["instanceId"], "after": watermark["sequence"]}).json()
    assert len(page["events"]) == 1
    event = page["events"][0]
    assert event["type"] == "RESERVATION_SUCCEEDED"
    assert event["orderId"] == body["orderId"]
    assert event["traceId"] == client.headers["X-Trace-Id"]
    assert TypeAdapter(Event).validate_python(event).type == "RESERVATION_SUCCEEDED"
    assert page["nextCursor"]["sequence"] == watermark["sequence"] + 1
    assert not page["hasMore"]
    snapshot = client.get("/snapshot").json()
    assert snapshot["watermark"] == page["nextCursor"]
    assert snapshot["reservations"][0] == event["payload"]
    assert json.loads(logs.getvalue().strip()) == event


def test_event_cursor_errors(client):
    snapshot = client.get("/snapshot").json()
    assert_error(client.get("/events", params={"instanceId": str(uuid4())}), 410, "EVENT_CURSOR_RESET")
    assert_error(client.get("/events", params={"instanceId": snapshot["instanceId"], "after": 999}), 400, "INVALID_CURSOR")
    assert_error(client.get("/events", params={"instanceId": snapshot["instanceId"], "after": -1}), 400, "INVALID_CURSOR")
    assert_error(client.get("/events", params={"instanceId": snapshot["instanceId"], "limit": 501}), 400, "INVALID_REQUEST")


def test_openapi_headers_and_typed_event_schema(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/reservations"]["post"]
    headers = {entry["name"]: entry for entry in operation["parameters"]}
    assert headers["X-Run-Id"]["required"]
    assert not headers["X-Trace-Id"]["required"]
    assert "400" in operation["responses"]
    assert "422" not in operation["responses"]
    assert schema["components"]["schemas"]["EventPage"]["properties"]["events"]["items"]["discriminator"]["propertyName"] == "type"


def test_http_concurrent_competition_and_duplicate_submission():
    async def scenario():
        settings = Settings(initial_stock=DEFAULT_STOCK | {"CHEESE": 3})
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://inventory",
                                         headers={"X-Run-Id": str(settings.run_id)}) as client:
                results = await asyncio.gather(*(client.post("/reservations", json=reservation()) for _ in range(12)))
                assert [result.status_code for result in results].count(200) == 3
                assert [result.status_code for result in results].count(409) == 9
                body = reservation({"DOUGH": 1})
                results = await asyncio.gather(*(client.post("/reservations", json=body) for _ in range(12)))
                assert all(result.status_code == 200 for result in results)
                assert len({result.json()["reservationId"] for result in results}) == 1
                stock = (await client.get("/stock")).json()["items"]
                assert stock["CHEESE"] == 0 and stock["DOUGH"] == 39
    asyncio.run(scenario())


def test_configured_quantity_boundary():
    settings = Settings(max_quantity=2)
    with TestClient(create_app(settings), headers={"X-Run-Id": str(settings.run_id)}) as client:
        assert client.post("/reservations", json=reservation({"CHEESE": 2})).status_code == 200
        assert_error(client.post("/reservations", json=reservation({"CHEESE": 3})), 400, "INVALID_REQUEST")


def test_settings_are_validated_and_environment_is_explicit(monkeypatch):
    run_id = uuid4()
    monkeypatch.setenv("PIZZAOPS_RUN_ID", str(run_id))
    monkeypatch.setenv("INVENTORY_MAX_QUANTITY", "12")
    assert Settings.from_env().run_id == run_id
    assert Settings.from_env().max_quantity == 12
    with pytest.raises(ValidationError):
        Settings(event_capacity=0)
    with pytest.raises(ValidationError):
        Settings(initial_stock=DEFAULT_STOCK | {"CHEESE": -1})
    with pytest.raises(ValidationError):
        Settings(initial_stock={"CHEESE": 1})


def test_unknown_route_and_method_keep_error_contract(client):
    assert_error(client.get("/missing"), 404, "NOT_FOUND")
    response = client.delete("/stock")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["Allow"]
