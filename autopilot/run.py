"""Run a shift against the live services. The autopilot is a plain HTTP client, like curl with a schedule:

    export PIZZAOPS_RUN_ID=...                      # printed by scripts/dev.py
    uv run python -m autopilot.run --scenario friday --seed 7
    uv run python -m autopilot.run --scenario calm --loop --time-scale 4

It places orders, restocks, switches Inventory faults and calls the kitchen's reconcile command at seeded times.
The same seed sends the same requests at the same scenario times; how the services interleave them may differ.
One JSON line per action goes to stdout. HTTP problems are logged and the shift goes on.
"""

import argparse
import json
import os
import random
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from autopilot.customers import Rate, arrivals, order_body, seeded_uuid
from autopilot.director import CALM, SCENARIOS
from autopilot.supplier import INTERVAL_S, Supplier

DEFAULT_URLS = {"order": "http://127.0.0.1:8080", "kitchen": "http://127.0.0.1:8081", "inventory": "http://127.0.0.1:8082"}


@dataclass(order=True, frozen=True)
class Action:
    at: float
    seq: int
    kind: str = field(compare=False)  # "order" | "supply" | "faults" | "reconcile" | "hold" | "release"
    data: dict = field(compare=False, default_factory=dict)


def timeline(scenario, seed, *, offset=0.0, supply_every=INTERVAL_S):
    """Every action of one scenario, in time order. Pure: the same scenario and seed give the same list."""
    walk_ins = random.Random(f"{seed}:{scenario.name}:walk-ins")
    crowd = random.Random(f"{seed}:{scenario.name}:crowd")
    rushes = tuple((cue.at, cue.args["end"], cue.args["factor"]) for cue in scenario.cues if cue.kind == "rush")
    entries = [(at, "order", {"body": body})
               for at, body in arrivals(crowd, 0.0, scenario.duration_s, Rate(scenario.rate_per_s, rushes), scenario.menu)]
    for cue in scenario.cues:
        if cue.kind == "order":
            args = dict(cue.args)
            entries.append((cue.at, "order", {"body": order_body(walk_ins, args.pop("pizza"), args.pop("quantity"), **args)}))
        elif cue.kind in ("faults", "reconcile", "hold", "release"):
            entries.append((cue.at, cue.kind, dict(cue.args)))
            if cue.kind == "release":
                entries.append((cue.at + 0.5, "supply", {}))  # the late truck arrives right away
    tick = supply_every
    while tick < scenario.duration_s:
        entries.append((tick, "supply", {}))
        tick += supply_every
    entries.sort(key=lambda entry: entry[0])
    return [Action(offset + at, index, kind, data) for index, (at, kind, data) in enumerate(entries)]


class Pilot:
    """Performs actions at their scenario times. ``time_scale`` > 1 compresses the schedule (tests, quick demos)."""

    def __init__(self, urls, run_id, *, seed=7, time_scale=1.0, client=None, stop=None, log=None):
        if time_scale <= 0:
            raise ValueError("time_scale must be positive")
        self.urls = urls
        self.headers = {"X-Run-Id": str(run_id)}
        self.time_scale = time_scale
        self.client = client or httpx.Client(timeout=httpx.Timeout(3.0, connect=0.5), trust_env=False)
        self.supplier = Supplier()
        self.commands = random.Random(f"{seed}:commands")
        self.stop = stop or threading.Event()
        self.log = log or (lambda line: print(line, flush=True))

    def close(self):
        self.client.close()

    def run(self, actions, start):
        """Perform ``actions`` against the clock that started at ``start``; False if stopped early."""
        for action in actions:
            delay = start + action.at / self.time_scale - time.monotonic()
            if delay > 0 and self.stop.wait(delay):
                return False
            if self.stop.is_set():
                return False
            self.perform(action)
        return True

    def perform(self, action):
        try:
            outcome = getattr(self, f"_{action.kind}")(action.data)
        except httpx.HTTPError as error:
            outcome = {"error": f"{type(error).__name__}: {error}"[:160]}
        self.log(json.dumps({"t": round(action.at, 2), "action": action.kind, **outcome}, separators=(",", ":")))
        return outcome

    def _post(self, service, path, body):
        return self.client.post(self.urls[service] + path, json=body, headers=self.headers)

    def _order(self, data):
        body = data["body"]
        response = self._post("order", "/orders", body)
        return {"status": response.status_code, "pizza": body["pizzaType"], "quantity": body["quantity"]}

    def _supply(self, data):
        response = self.client.get(self.urls["inventory"] + "/stock", params={"authoritative": "true"}, headers=self.headers)
        if response.status_code != 200:
            return {"status": response.status_code, "delivered": {}}
        items = self.supplier.delivery_for(response.json()["items"])
        if not items:
            return {"status": 200, "delivered": {}}
        restock = self._post("inventory", "/admin/restock", {"commandId": seeded_uuid(self.commands), "items": items})
        return {"status": restock.status_code, "delivered": items if restock.status_code == 200 else {}}

    def _faults(self, data):
        response = self._post("inventory", "/admin/faults", {"commandId": seeded_uuid(self.commands), **data})
        return {"status": response.status_code, **data}

    def _reconcile(self, data):
        response = self._post("kitchen", "/admin/reconcile", {"commandId": seeded_uuid(self.commands)})
        return {"status": response.status_code}

    def _hold(self, data):
        self.supplier.held.add(data["ingredient"])
        return {"held": sorted(self.supplier.held)}

    def _release(self, data):
        self.supplier.held.discard(data["ingredient"])
        return {"held": sorted(self.supplier.held)}


def capped(actions, duration):
    return [action for action in actions if duration is None or action.at < duration]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="friday")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--time-scale", type=float, default=1.0, help="run the schedule this many times faster")
    parser.add_argument("--duration", type=float, help="stop after this many scenario seconds")
    parser.add_argument("--loop", action="store_true", help="after the scenario, keep a calm restaurant running")
    parser.add_argument("--run-id", help="defaults to PIZZAOPS_RUN_ID")
    parser.add_argument("--order-url", default=DEFAULT_URLS["order"])
    parser.add_argument("--kitchen-url", default=DEFAULT_URLS["kitchen"])
    parser.add_argument("--inventory-url", default=DEFAULT_URLS["inventory"])
    args = parser.parse_args(argv)
    run_id = args.run_id or os.environ.get("PIZZAOPS_RUN_ID")
    if not run_id:
        parser.error("needs --run-id or PIZZAOPS_RUN_ID")
    UUID(run_id)
    if args.time_scale <= 0:
        parser.error("--time-scale must be positive")

    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    urls = {"order": args.order_url, "kitchen": args.kitchen_url, "inventory": args.inventory_url}
    pilot = Pilot(urls, run_id, seed=args.seed, time_scale=args.time_scale, stop=stop)
    scenario = SCENARIOS[args.scenario]
    start = time.monotonic()
    try:
        finished = pilot.run(capped(timeline(scenario, args.seed), args.duration), start)
        # After the scenario, calm evenings follow one another until stopped (each generated when it starts).
        offset, index = scenario.duration_s, 0
        while finished and args.loop and (args.duration is None or offset < args.duration):
            finished = pilot.run(capped(timeline(CALM, f"{args.seed}:after:{index}", offset=offset), args.duration), start)
            offset += CALM.duration_s
            index += 1
    finally:
        pilot.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
