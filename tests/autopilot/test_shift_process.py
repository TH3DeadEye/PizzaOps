"""A compressed Friday night against three real service processes, driven by the autopilot's own command line."""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

from scripts.dev import Launcher

ROOT = Path(__file__).resolve().parents[2]
FAST = {"KITCHEN_COOKING_S": "0.05"}


def settled_orders(client, url, headers, expected, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        orders = client.get(url + "/orders", headers=headers, params={"limit": 100}).json()["orders"]
        if len(orders) >= expected and all(order["status"] in ("READY", "FAILED") for order in orders):
            return orders
        if time.monotonic() > deadline:
            return orders
        time.sleep(0.2)


def test_a_compressed_friday_shift_runs_against_real_services(tmp_path):
    with Launcher(ports={"order": 0, "kitchen": 0, "inventory": 0}, log_dir=tmp_path, extra_env=FAST) as launcher:
        urls = launcher.urls
        result = subprocess.run(
            [sys.executable, "-m", "autopilot.run", "--scenario", "friday", "--time-scale", "40", "--duration", "215",
             "--run-id", launcher.run_id, "--order-url", urls["order"], "--kitchen-url", urls["kitchen"],
             "--inventory-url", urls["inventory"]],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        orders = [line for line in lines if line["action"] == "order"]
        assert orders and all(line["status"] == 201 for line in orders)
        assert all(line["status"] == 200 for line in lines if line["action"] in ("faults", "reconcile"))
        assert any(line["action"] == "supply" and line["delivered"].get("PEPPERONI") for line in lines)

        with httpx.Client(trust_env=False, timeout=3) as client:
            final = settled_orders(client, urls["order"], {"X-Run-Id": launcher.run_id}, len(orders))
        statuses = [order["status"] for order in final]
        assert len(final) == len(orders) and set(statuses) == {"READY", "FAILED"}
        assert any(order["failureReason"] == "INSUFFICIENT_STOCK" and order["pizzaType"] == "PEPPERONI" for order in final)
    kitchen_log = (tmp_path / "kitchen.log").read_text()
    assert '"RELATIONSHIP_CHANGED"' in kitchen_log  # the stale sheet was caught at least once


def test_play_opens_the_whole_restaurant_headless_and_closes_it(tmp_path):
    screenshot = tmp_path / "play.png"
    result = subprocess.run(
        [sys.executable, "scripts/play.py", "--headless", "--frames", "150", "--screenshot", str(screenshot),
         "--cooking-s", "0.2", "--time-scale", "10", "--log-dir", str(tmp_path / "logs")],
        cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert screenshot.exists() and screenshot.stat().st_size > 0
    autopilot = [json.loads(line) for line in (tmp_path / "logs" / "autopilot.log").read_text().splitlines() if line.startswith("{")]
    assert any(line["action"] == "order" and line["status"] == 201 for line in autopilot)
    assert "Closed the restaurant." in result.stdout
