"""The local launcher starts three real processes under one run ID and leaves none behind."""

import os
import time

import httpx
import pytest

from scripts.dev import LaunchError, Launcher


def test_launcher_opens_the_restaurant_and_stops_cleanly(tmp_path):
    launcher = Launcher(ports={"order": 0, "kitchen": 0, "inventory": 0}, log_dir=tmp_path,
                        extra_env={"KITCHEN_COOKING_S": "0.05"})
    pids = []
    with launcher:
        pids.extend(service.process.pid for service in launcher.services)
        for name, base in launcher.urls.items():
            health = httpx.get(f"{base}/health", timeout=2, trust_env=False)
            assert health.status_code == 200 and health.json()["runId"] == launcher.run_id, name
        body = {"requestId": "11111111-1111-4111-8111-111111111111", "pizzaType": "CHEESE", "quantity": 1,
                "customer": {"id": "22222222-2222-4222-8222-222222222222", "appearance": "customer_amber", "trait": "patient"}}
        placed = httpx.post(f"{launcher.urls['order']}/orders", json=body, headers={"X-Run-Id": launcher.run_id},
                            timeout=3, trust_env=False)
        assert placed.status_code == 201, placed.text
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = httpx.get(f"{launcher.urls['order']}/orders/{placed.json()['orderId']}",
                               headers={"X-Run-Id": launcher.run_id}, timeout=2, trust_env=False).json()["status"]
            if status == "READY":
                break
            time.sleep(0.05)
        else:
            pytest.fail(status)
    time.sleep(0.2)
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_launcher_refuses_a_port_that_is_already_a_different_run(tmp_path):
    first = Launcher(ports={"order": 0, "kitchen": 0, "inventory": 0}, log_dir=tmp_path / "a",
                     extra_env={"KITCHEN_COOKING_S": "0.05"})
    with first:
        with pytest.raises(LaunchError, match="another run|did not become ready|exited"):
            Launcher(ports=first.ports, log_dir=tmp_path / "b").start()
