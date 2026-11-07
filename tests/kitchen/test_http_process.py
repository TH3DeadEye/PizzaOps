import json
import time
from uuid import uuid4

import httpx
import pytest

from tests.processes import ServiceProcess, clean_environment, free_port


@pytest.fixture
def services(tmp_path):
    run_id = str(uuid4())
    inventory_port, kitchen_port = free_port(), free_port()
    inventory = ServiceProcess("inventory", "services.inventory.main", inventory_port, tmp_path / "inventory.log",
                               clean_environment(PIZZAOPS_RUN_ID=run_id), run_id)
    kitchen = ServiceProcess("kitchen", "services.kitchen.main", kitchen_port, tmp_path / "kitchen.log",
                             clean_environment(PIZZAOPS_RUN_ID=run_id, KITCHEN_INVENTORY_URL=f"http://127.0.0.1:{inventory_port}",
                                               KITCHEN_COOKING_S=0.05, KITCHEN_ATTEMPT_TIMEOUT_S=1, KITCHEN_RETRY_BACKOFF_S=0.05),
                             run_id)
    try:
        inventory.start()
        kitchen.start()
        yield inventory, kitchen, {"X-Run-Id": run_id}
    finally:
        kitchen.stop()
        inventory.stop()


def events_in(process):
    return [json.loads(line) for line in process.log_lines() if line.startswith("{")]


def wait_until(client, order_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/cook/{order_id}")
        assert response.status_code == 200, response.text
        if response.json()["status"] in ("COOKED", "FAILED", "UNKNOWN"):
            return response.json()
        assert time.monotonic() < deadline, response.text
        time.sleep(0.01)


def test_real_processes_cook_share_a_trace_and_fail_cleanly_without_inventory(services):
    inventory, kitchen, headers = services
    trace_id = str(uuid4())
    with httpx.Client(base_url=kitchen.base_url, headers=headers, timeout=3, trust_env=False) as client, \
            httpx.Client(base_url=inventory.base_url, headers=headers, timeout=3, trust_env=False) as stockroom:
        body = {"orderId": str(uuid4()), "pizzaType": "PEPPERONI", "quantity": 2}
        started = time.monotonic()
        accepted = client.post("/cook", json=body, headers={"X-Trace-Id": trace_id})
        assert accepted.status_code == 202, accepted.text
        assert accepted.headers["X-Trace-Id"] == trace_id and accepted.headers["Location"] == f"/cook/{body['orderId']}"
        done = wait_until(client, body["orderId"])
        elapsed = time.monotonic() - started
        assert done["status"] == "COOKED" and done["traceId"] == trace_id, done
        assert elapsed < 3, f"happy path took {elapsed:.2f}s"
        assert stockroom.get("/stock").json()["items"]["PEPPERONI"] == 13
        assert client.post("/cook", json=body).status_code == 200

        shortage = {"orderId": str(uuid4()), "pizzaType": "CHEESE", "quantity": 20}
        assert client.post("/cook", json=shortage).status_code == 202
        assert wait_until(client, shortage["orderId"])["status"] == "COOKED"
        shortage = {"orderId": str(uuid4()), "pizzaType": "CHEESE", "quantity": 20}
        assert client.post("/cook", json=shortage).status_code == 202
        failed = wait_until(client, shortage["orderId"])
        assert failed["status"] == "FAILED" and failed["failureReason"] == "INSUFFICIENT_STOCK"
        assert stockroom.get("/stock").json()["items"]["CHEESE"] == 8

        inventory.stop()
        body = {"orderId": str(uuid4()), "pizzaType": "MARINARA", "quantity": 1}
        started = time.monotonic()
        assert client.post("/cook", json=body).status_code == 202
        unavailable = wait_until(client, body["orderId"])
        assert unavailable["status"] == "FAILED" and unavailable["failureReason"] == "INVENTORY_UNAVAILABLE"
        assert unavailable["lastError"]["code"] == "CONNECTION_FAILED"
        assert time.monotonic() - started < 4
        assert client.get("/health").status_code == 200

    kitchen_events = events_in(kitchen)
    inventory_events = events_in(inventory)
    assert {event["type"] for event in kitchen_events if event["traceId"] == trace_id} >= {
        "COOK_QUEUED", "COOK_CHECKING", "STOCK_CHECKED", "COOK_STARTED", "COOK_COMPLETED"}
    assert {event["type"] for event in inventory_events if event["traceId"] == trace_id} == {"STOCK_READ", "RESERVATION_SUCCEEDED"}
    assert all(event["service"] == "kitchen" for event in kitchen_events)
    assert any(event["type"] == "DEPENDENCY_UNAVAILABLE" for event in kitchen_events)
