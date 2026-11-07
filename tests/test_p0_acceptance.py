"""Milestone P0: one customer order becomes READY through three independent HTTP processes.

Covers the guide's Step 4 checklist and the first two rows of the proposal's test table: the normal path and
insufficient stock, plus a stopped Kitchen and liveness on all three ports.
"""

import json
import time
from uuid import uuid4

import httpx
import pytest

from tests.processes import ServiceProcess, clean_environment, free_port

SETTLED = ("READY", "FAILED", "UNKNOWN")


@pytest.fixture
def restaurant(tmp_path):
    run_id = str(uuid4())
    inventory_port, kitchen_port, order_port = free_port(), free_port(), free_port()
    inventory = ServiceProcess("inventory", "services.inventory.main", inventory_port, tmp_path / "inventory.log",
                               clean_environment(PIZZAOPS_RUN_ID=run_id), run_id)
    kitchen = ServiceProcess("kitchen", "services.kitchen.main", kitchen_port, tmp_path / "kitchen.log",
                             clean_environment(PIZZAOPS_RUN_ID=run_id, KITCHEN_INVENTORY_URL=f"http://127.0.0.1:{inventory_port}",
                                               KITCHEN_COOKING_S=0.1, KITCHEN_ATTEMPT_TIMEOUT_S=1, KITCHEN_RETRY_BACKOFF_S=0.05),
                             run_id)
    order = ServiceProcess("order", "services.order.main", order_port, tmp_path / "order.log",
                           clean_environment(PIZZAOPS_RUN_ID=run_id, ORDER_KITCHEN_URL=f"http://127.0.0.1:{kitchen_port}",
                                             ORDER_ATTEMPT_TIMEOUT_S=1, ORDER_RETRY_BACKOFF_S=0.05, ORDER_POLL_INTERVAL_S=0.05),
                           run_id)
    try:
        inventory.start()
        kitchen.start()
        order.start()
        yield inventory, kitchen, order, {"X-Run-Id": run_id}
    finally:
        order.stop()
        kitchen.stop()
        inventory.stop()


def place(client, pizza, quantity, trace_id=None):
    body = {"requestId": str(uuid4()), "pizzaType": pizza, "quantity": quantity,
            "customer": {"id": str(uuid4()), "appearance": "customer_teal", "trait": "impatient"}}
    headers = {"X-Trace-Id": trace_id} if trace_id else {}
    response = client.post("/orders", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def settle(client, order_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/orders/{order_id}")
        assert response.status_code == 200, response.text
        if response.json()["status"] in SETTLED:
            return response.json()
        assert time.monotonic() < deadline, response.text
        time.sleep(0.02)


def events_in(process):
    return [json.loads(line) for line in process.log_lines() if line.startswith("{")]


def test_p0_one_order_becomes_ready_across_three_processes(restaurant):
    inventory, kitchen, order, headers = restaurant
    for service in (inventory, kitchen, order):
        health = httpx.get(f"{service.base_url}/health", timeout=2, trust_env=False)
        assert health.status_code == 200 and health.json()["status"] == "UP"

    trace_id = str(uuid4())
    with httpx.Client(base_url=order.base_url, headers=headers, timeout=3, trust_env=False) as waiter, \
            httpx.Client(base_url=inventory.base_url, headers=headers, timeout=3, trust_env=False) as stockroom:
        started = time.monotonic()
        placed = place(waiter, "PEPPERONI", 2, trace_id)
        assert placed["status"] == "RECEIVED" and placed["traceId"] == trace_id
        ready = settle(waiter, placed["orderId"])
        elapsed = time.monotonic() - started
        assert ready["status"] == "READY" and ready["failureReason"] is None and ready["traceId"] == trace_id, ready
        assert ready["kitchenStatus"] == "COOKED" and ready["reservationId"] and ready["jobId"]
        assert elapsed < 3, f"happy path took {elapsed:.2f}s"
        assert stockroom.get("/stock").json()["items"] == {"DOUGH": 38, "SAUCE": 38, "CHEESE": 28, "PEPPERONI": 13, "BASIL": 15}

        # insufficient stock: the second batch of twenty cheese pizzas cannot be made
        assert settle(waiter, place(waiter, "CHEESE", 20)["orderId"])["status"] == "READY"
        failed = settle(waiter, place(waiter, "CHEESE", 20)["orderId"])
        assert failed["status"] == "FAILED" and failed["failureReason"] == "INSUFFICIENT_STOCK"
        assert failed["lastError"]["details"]["jobError"]["details"]["missing"]["CHEESE"] == 12
        assert settle(waiter, place(waiter, "MARINARA", 1)["orderId"])["status"] == "READY"

        listing = waiter.get("/orders", params={"limit": 2}).json()
        assert [item["pizzaType"] for item in listing["orders"]] == ["MARINARA", "CHEESE"] and listing["nextCursor"]

        # the kitchen goes down: a clean, known failure within the budget, never a hang or a fake READY
        kitchen.stop()
        started = time.monotonic()
        down = settle(waiter, place(waiter, "CHEESE", 1)["orderId"])
        assert down["status"] == "FAILED" and down["failureReason"] == "KITCHEN_UNAVAILABLE"
        assert down["lastError"]["code"] == "CONNECTION_FAILED" and down["jobId"] is None
        assert time.monotonic() - started < 4
        assert waiter.get("/health").status_code == 200

    # one trace ID tells the whole story in all three logs
    for process, expected in ((order, {"ORDER_RECEIVED", "ORDER_CHECKING", "ORDER_READY"}),
                              (kitchen, {"COOK_QUEUED", "COOK_CHECKING", "STOCK_CHECKED", "COOK_STARTED", "COOK_COMPLETED"}),
                              (inventory, {"STOCK_READ", "RESERVATION_SUCCEEDED"})):
        lines = [line for line in process.log_lines() if trace_id in line]
        assert lines, f"{process.name} log has no line for trace {trace_id}"
        assert {event["type"] for event in events_in(process) if event["traceId"] == trace_id} >= expected
    assert all(event["service"] == "order" for event in events_in(order))
    assert {event["orderId"] for event in events_in(order) if event["traceId"] == trace_id} == {ready["orderId"]}
