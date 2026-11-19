"""The restaurant as the viewer understands it: service records, stock, mood, and the stage that acts them out.

Pure Python: no pygame, no network. Three rules keep the picture honest:

- An event is applied once. Identity is ``(service, instanceId, sequence)``.
- A service-owned record (order, job, stock, mood) only moves forward: an older version never overwrites a newer one.
  A snapshot from a new service instance replaces that service's records wholesale, because the old ones are gone.
- The stage (``viewer/stage.py``) acts events out. It may run a few seconds behind so things happen in a believable
  order, but it never decides an outcome: a customer only walks out with a pizza box after ORDER_READY.
"""

from collections import deque
from dataclasses import dataclass

from viewer import narration
from viewer.layout import Layout
from viewer.stage import Actor, Stage  # noqa: F401  (Actor stays importable from here)
from viewer.updates import Event, Resync, SERVICES, Snapshot, Status

MAX_SEEN = 20_000


@dataclass
class ServiceView:
    state: str = "DISCONNECTED"
    detail: str | None = None
    instance_id: str | None = None
    run_id: str | None = None
    last_sequence: int = 0


def short(identifier):
    return str(identifier)[:8] if identifier else "-"


class World:
    def __init__(self, scene=None):
        self.layout = Layout(scene)
        self.stage = Stage(self.layout)
        self.time = 0.0
        self.orders = {}
        self.jobs = {}
        self.stock = {}
        self.stock_version = 0
        self.stock_uncertain = False
        self.reservations = 0
        self.faults = None
        self.mood = None
        self.menu = []
        self.recipes = {}
        self.kitchen_queue = None
        self.services = {name: ServiceView() for name in SERVICES}
        self.seen = {name: set() for name in SERVICES}
        self.ticker = deque(maxlen=14)
        self.story = deque(maxlen=12)  # (time, narration.Story), newest last
        self.resyncs = 0

    # ------------------------------------------------------------------ the cast (owned by the stage)

    @property
    def actors(self):
        return self.stage.actors

    @property
    def customers(self):
        return self.stage.customers

    @property
    def entrance(self):
        return self.stage.entrance

    @property
    def wait_slots(self):
        return self.stage.wait_slots

    @property
    def overflow(self):
        return self.stage.overflow

    @property
    def table_seats(self):
        return self.stage.table_seats

    @property
    def homes(self):
        return self.stage.homes

    @property
    def oven_spot(self):
        return self.stage.oven_spot

    @property
    def pass_spot(self):
        return self.stage.pass_spot

    @property
    def crates_spot(self):
        return self.stage.crates_spot

    def everyone(self):
        return self.stage.everyone()

    # ------------------------------------------------------------------ queries

    @property
    def mood_label(self):
        return (self.mood or {}).get("mood", "CALM")

    @property
    def counters(self):
        tally = {"open": 0, "ready": 0, "failed": 0, "unknown": 0}
        for order in self.orders.values():
            status = order["status"]
            tally["ready" if status == "READY" else "failed" if status == "FAILED" else "unknown" if status == "UNKNOWN" else "open"] += 1
        return tally

    def recent_orders(self, limit=8):
        return sorted(self.orders.values(), key=lambda order: order["sequence"], reverse=True)[:limit]

    # ------------------------------------------------------------------ updates

    def apply(self, update):
        if isinstance(update, Status):
            view = self.services[update.service]
            if (view.state, view.detail) != (update.state, update.detail):
                self._note(update.service, update.state, update.detail)
                self._tell(narration.status_story(update.service, update.state, view.state))
            view.state, view.detail = update.state, update.detail
        elif isinstance(update, Resync):
            self.resyncs += 1
            self._note("feed", "RESYNC", "updates were dropped; taking fresh snapshots")
            self._tell(narration.Story("Missed some updates; catching up", "ui_warning", "warn"))
        elif isinstance(update, Snapshot):
            self._apply_snapshot(update.service, update.data)
        elif isinstance(update, Event):
            return self._apply_event(update.service, update.data)
        return True

    def _apply_snapshot(self, service, data):
        view = self.services[service]
        new_instance = view.instance_id != data["instanceId"]
        if new_instance:
            self.seen[service].clear()
            if view.instance_id is not None:
                self._note(service, "NEW_INSTANCE", f"{short(data['instanceId'])} replaced {short(view.instance_id)}")
        view.instance_id, view.run_id = data["instanceId"], data["runId"]
        view.last_sequence = max(data["watermark"]["sequence"], 0 if new_instance else view.last_sequence)
        if service == "order":
            if new_instance:
                self.orders = {}
            for record in data["orders"]:
                self._put_order(record)
            self.menu = list(data["menu"])
        elif service == "kitchen":
            if new_instance:
                self.jobs = {}
            for job in data["jobs"]:
                self._put_job(job)
            self._put_mood(data["mood"], force=new_instance)
            self.recipes = data["recipes"]
            self.kitchen_queue = data.get("queue")
        elif service == "inventory":
            self.stock = dict(data["items"])
            self.stock_version = data["stockVersion"]
            self.stock_uncertain = False
            self.reservations = len(data["reservations"])
            self.faults = data["faults"]
        self.stage.reconcile(self)

    def _apply_event(self, service, event):
        view = self.services[service]
        key = (event["instanceId"], event["sequence"])
        if view.instance_id is not None and event["instanceId"] != view.instance_id:
            self.seen[service].clear()
            view.instance_id = event["instanceId"]
        if key in self.seen[service]:
            return False
        if len(self.seen[service]) >= MAX_SEEN:
            self.seen[service] = {item for item in self.seen[service] if item[1] > event["sequence"] - MAX_SEEN // 2}
        self.seen[service].add(key)
        view.last_sequence = max(view.last_sequence, event["sequence"])
        story = narration.narrate(service, event, self)  # before projecting, so changes compare with the old state
        self._project(service, event)
        self._tell(story)
        self.stage.react(service, event, self)
        self._ticker(service, event)
        return True

    # ------------------------------------------------------------------ records

    def _put_order(self, record):
        current = self.orders.get(record["orderId"])
        if current is not None and current["version"] >= record["version"]:
            return False
        self.orders[record["orderId"]] = record
        return True

    def _put_job(self, record):
        current = self.jobs.get(record["orderId"])
        if current is not None and current["version"] >= record["version"]:
            return False
        self.jobs[record["orderId"]] = record
        return True

    def _put_mood(self, mood, force=False):
        if force or self.mood is None or mood["version"] >= self.mood["version"]:
            self.mood = mood

    def _project(self, service, event):
        kind, payload = event["type"], event["payload"]
        if service == "order" and kind.startswith("ORDER_"):
            self._put_order(payload)
        elif service == "kitchen":
            if kind.startswith("COOK_"):
                self._put_job(payload)
            elif kind in ("MOOD_CHANGED", "RELATIONSHIP_CHANGED"):
                self._put_mood(payload["after"])
        elif service == "inventory":
            if kind == "STOCK_READ" and payload["source"] == "AUTHORITATIVE" and payload["stockVersion"] >= self.stock_version:
                self.stock.update(payload["items"])
                self.stock_version = payload["stockVersion"]
                if set(payload["items"]) >= set(self.stock):
                    self.stock_uncertain = False
            elif kind == "RESERVATION_SUCCEEDED":
                version = payload["stockVersion"]
                if version == self.stock_version + 1 and self.stock:
                    for name, quantity in payload["items"].items():
                        self.stock[name] = self.stock.get(name, 0) - quantity
                    self.stock_version = version
                elif version > self.stock_version:
                    self.stock_uncertain = True
                self.reservations += 1
            elif kind == "STOCK_RESTOCKED" and payload["stockVersion"] >= self.stock_version:
                self.stock = dict(payload["items"])
                self.stock_version = payload["stockVersion"]
                self.stock_uncertain = False
            elif kind == "FAULT_CONFIG_CHANGED":
                self.faults = payload["faults"]

    # ------------------------------------------------------------------ time

    def tick(self, dt):
        self.time += dt
        self.stage.tick(dt, self.time, self)

    # ------------------------------------------------------------------ ticker

    def _tell(self, story):
        if story is None:
            return
        if any(told.text == story.text and self.time - when < 10 for when, told in self.story):
            return  # an outage repeats itself; say it once
        self.story.append((self.time, story))

    def _note(self, source, kind, detail=None):
        self.ticker.append({"time": self.time, "service": source, "type": kind, "detail": detail or "", "orderId": None})

    def _ticker(self, service, event):
        payload, kind = event["payload"], event["type"]
        detail = ""
        if kind.startswith(("ORDER_", "COOK_")):
            detail = f"{payload.get('pizzaType', '')} x{payload.get('quantity', '')}".strip()
            if payload.get("failureReason"):
                detail += f"  {payload['failureReason']}"
        elif kind == "STOCK_CHECKED":
            detail = ("enough" if payload["sufficient"] else "short: " + ", ".join(payload["missing"])) + f"  ({payload['source'].lower()})"
        elif kind == "RESERVATION_REJECTED":
            detail = payload["code"]
        elif kind in ("MOOD_CHANGED", "RELATIONSHIP_CHANGED"):
            after = payload["after"]
            detail = f"{after['mood']}  trust {after['trust']}  patience {after['patience']}"
        elif kind.startswith("DEPENDENCY_"):
            detail = f"{payload['operation']}: {payload['code']}"
        elif kind == "FAULT_CONFIG_CHANGED":
            faults = payload["faults"]
            detail = f"stale={faults['staleStockEnabled']} delay={faults['delayMs']} mode={faults['failureMode']}"
        self.ticker.append({"time": self.time, "service": service, "type": kind, "detail": detail,
                            "orderId": event.get("orderId"), "traceId": event.get("traceId")})
