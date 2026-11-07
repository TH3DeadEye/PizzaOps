import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def server(tmp_path):
    run_id = str(uuid4())
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "inventory.log"
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith("INVENTORY_"):
            del environment[name]
    environment["PIZZAOPS_RUN_ID"] = run_id
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "services.inventory.main:app", "--host", "127.0.0.1", "--port", str(port),
             "--log-level", "warning", "--no-access-log", "--timeout-graceful-shutdown", "2"],
            cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            with httpx.Client(base_url=base_url, timeout=0.3, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"Inventory exited during startup: {log_path.read_text()}")
                    try:
                        response = client.get("/health")
                        if response.status_code == 200:
                            assert response.json()["runId"] == run_id
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.025)
                else:
                    pytest.fail(f"Inventory did not become ready: {log_path.read_text()}")
            yield base_url, {"X-Run-Id": run_id}, log_path
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_real_http_concurrency_and_trace_logs(server):
    base_url, headers, log_path = server
    trace_id = str(uuid4())
    async def scenario():
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=2, trust_env=False) as client:
            original = await client.post("/reservations", json={"orderId": str(uuid4()), "items": {"CHEESE": 27}},
                                         headers={"X-Trace-Id": trace_id})
            assert original.status_code == 200
            responses = await asyncio.gather(*(client.post("/reservations", json={"orderId": str(uuid4()), "items": {"CHEESE": 1}})
                                               for _ in range(12)))
            assert [response.status_code for response in responses].count(200) == 3
            assert [response.status_code for response in responses].count(409) == 9
            order = {"orderId": str(uuid4()), "items": {"DOUGH": 1}}
            duplicates = await asyncio.gather(*(client.post("/reservations", json=order) for _ in range(12)))
            assert all(response.status_code == 200 for response in duplicates)
            assert len({response.json()["reservationId"] for response in duplicates}) == 1
            snapshot = (await client.get("/snapshot")).json()
            assert snapshot["items"]["CHEESE"] == 0 and snapshot["items"]["DOUGH"] == 39
            assert len(snapshot["reservations"]) == 5
    asyncio.run(scenario())
    events = [json.loads(line) for line in log_path.read_text().splitlines() if line.startswith("{")]
    assert any(event["traceId"] == trace_id and event["type"] == "RESERVATION_SUCCEEDED" for event in events)


def test_real_http_lost_response_reconciles_without_double_deduction(server):
    base_url, headers, _ = server
    with httpx.Client(base_url=base_url, headers=headers, timeout=2, trust_env=False) as client:
        config = {"commandId": str(uuid4()), "staleStockEnabled": False, "delayMs": 800,
                  "failureMode": "NONE", "delayPhase": "AFTER_MUTATION_BEFORE_RESPONSE"}
        assert client.post("/admin/faults", json=config).status_code == 200
        order = {"orderId": str(uuid4()), "items": {"CHEESE": 1}}
        with pytest.raises(httpx.ReadTimeout):
            client.post("/reservations", json=order, timeout=0.15)
        snapshot = client.get("/snapshot").json()
        assert snapshot["items"]["CHEESE"] == 29
        assert len(snapshot["reservations"]) == 1
        assert client.post("/admin/faults", json=config | {"commandId": str(uuid4()), "delayMs": 0}).status_code == 200
        original = client.get(f"/reservations/{order['orderId']}").json()
        assert client.post("/reservations", json=order).json() == original
        assert client.get("/stock").json()["items"]["CHEESE"] == 29
