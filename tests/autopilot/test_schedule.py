"""The autopilot's schedule is pure and seeded, and every request it sends matches the services' contracts."""

import json
import random
import threading
from uuid import uuid4

import httpx

from autopilot.customers import Rate, arrivals
from autopilot.director import CALM, FRIDAY
from autopilot.run import Action, Pilot, capped, timeline
from autopilot.supplier import Supplier
from services.inventory.models import FaultRequest, RestockRequest
from services.kitchen.models import ReconcileRequest
from services.order.models import OrderRequest
from services.order.settings import DEFAULT_MENU


def flat(actions):
    return [(action.at, action.kind, json.dumps(action.data, sort_keys=True)) for action in actions]


def test_the_same_seed_gives_the_same_shift_and_another_seed_does_not():
    assert flat(timeline(FRIDAY, 7)) == flat(timeline(FRIDAY, 7))
    assert flat(timeline(FRIDAY, 7)) != flat(timeline(FRIDAY, 8))
    actions = timeline(FRIDAY, 7)
    assert [action.at for action in actions] == sorted(action.at for action in actions)
    assert all(0 <= action.at < FRIDAY.duration_s + 1 for action in actions)
    shifted = timeline(CALM, 3, offset=300)
    assert min(action.at for action in shifted) >= 300
    assert all(action.at < 100 for action in capped(actions, 100))


def test_every_request_body_matches_the_service_contracts():
    for action in timeline(FRIDAY, 7) + timeline(CALM, 7):
        if action.kind == "order":
            request = OrderRequest.model_validate(action.data["body"])
            assert request.pizza_type in DEFAULT_MENU
        elif action.kind == "faults":
            FaultRequest.model_validate({"commandId": str(uuid4()), **action.data})
    ReconcileRequest.model_validate({"commandId": str(uuid4())})
    RestockRequest.model_validate({"commandId": str(uuid4()), "items": Supplier().delivery_for({"DOUGH": 1, "CHEESE": 0})})


def test_a_rush_brings_more_customers():
    found = arrivals(random.Random(1), 0, 3000, Rate(0.05, ((1000, 2000, 3.0),)), {"CHEESE": 1})
    inside = sum(1 for at, _ in found if 1000 <= at < 2000)
    outside = len(found) - inside
    assert 2.0 < inside / (outside / 2) < 4.0


def test_the_supplier_tops_up_what_is_low_and_respects_a_hold():
    stock = {"DOUGH": 5, "SAUCE": 40, "CHEESE": 9, "PEPPERONI": 0, "BASIL": 4}
    assert Supplier().delivery_for(stock) == {"BASIL": 11, "DOUGH": 35, "PEPPERONI": 15}
    assert "PEPPERONI" not in Supplier(held={"PEPPERONI"}).delivery_for(stock)
    assert Supplier().delivery_for({"DOUGH": 40}) == {}


def test_friday_night_tells_its_story():
    actions = timeline(FRIDAY, 7)
    stale = [(action.at, action.data["staleStockEnabled"]) for action in actions if action.kind == "faults"]
    assert stale[0] == (45, True) and (200, False) in stale
    assert any(action.kind == "hold" for action in actions) and any(action.kind == "release" for action in actions)
    release = next(action.at for action in actions if action.kind == "release")
    assert any(action.kind == "supply" and release < action.at <= release + 1 for action in actions)
    parties = [action.data["body"]["quantity"] for action in actions
               if action.kind == "order" and action.data["body"]["quantity"] >= 5]
    assert parties == [6, 5]
    outage = next(action.at for action in actions if action.kind == "faults" and action.data["failureMode"] == "UNAVAILABLE")
    assert any(action.kind == "reconcile" and action.at > outage for action in actions)


def test_the_pilot_sends_the_run_id_logs_every_action_and_survives_errors():
    seen, lines = [], []

    def handler(request):
        seen.append(request)
        if request.url.path == "/stock":
            return httpx.Response(200, json={"items": {"DOUGH": 2, "SAUCE": 40, "CHEESE": 30, "PEPPERONI": 15, "BASIL": 15},
                                             "source": "AUTHORITATIVE", "stockVersion": 3, "snapshotVersion": 3})
        if request.url.path == "/admin/reconcile":
            raise httpx.ConnectError("kitchen is down")
        return httpx.Response(201 if request.url.path == "/orders" else 200, json={})

    urls = {"order": "http://order", "kitchen": "http://kitchen", "inventory": "http://inventory"}
    pilot = Pilot(urls, "11111111-1111-4111-8111-111111111111", time_scale=1e6,
                  client=httpx.Client(transport=httpx.MockTransport(handler)), log=lines.append)
    actions = [action for action in timeline(FRIDAY, 7) if action.at < 60] + [Action(61, 999, "reconcile", {})]
    assert pilot.run(actions, start=0) is True
    assert len(lines) == len(actions)
    assert all(request.headers["X-Run-Id"] == "11111111-1111-4111-8111-111111111111" for request in seen)
    restocks = [json.loads(request.content) for request in seen if request.url.path == "/admin/restock"]
    assert restocks and all(body["items"] == {"DOUGH": 38} for body in restocks)
    assert json.loads(lines[-1])["error"].startswith("ConnectError")

    stopped = threading.Event()
    stopped.set()
    quiet = Pilot(urls, uuid4(), client=httpx.Client(transport=httpx.MockTransport(handler)), stop=stopped, log=lines.append)
    assert quiet.run(timeline(CALM, 1), start=0) is False
