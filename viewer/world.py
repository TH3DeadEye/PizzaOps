"""The restaurant as the viewer understands it: service records, stock, mood, and the actors who act them out.

Pure Python: no pygame, no network. Three rules keep the picture honest:

- An event is applied once. Identity is ``(service, instanceId, sequence)``.
- A service-owned record (order, job, stock, mood) only moves forward: an older version never overwrites a newer one.
  A snapshot from a new service instance replaces that service's records wholesale, because the old ones are gone.
- Actors decorate events. They never decide outcomes: a customer only walks out with a pizza box after ORDER_READY.
"""

from collections import deque
from dataclasses import dataclass, field

from viewer import dialogue
from viewer.geometry import step_towards
from viewer.updates import Event, Resync, SERVICES, Snapshot, Status

TERMINAL = {"READY", "FAILED"}
WALK_SPEED = 1.6  # tiles per second
MAX_SEEN = 20_000

ENTRANCE = (11.4, 11.0)
WAIT_SLOTS = [(5.7, 5.3), (6.6, 5.9), (7.5, 6.5), (8.4, 7.1), (9.3, 7.7), (10.2, 8.3)]
OVERFLOW_SPOT = (10.7, 9.6)
TABLE_SEATS = [(8.1, 7.4), (6.9, 4.4), (10.3, 8.9)]
HOMES = {"chef": ((2.8, 3.7), "sw"), "cashier": ((3.4, 5.2), "se"), "stock_worker": ((6.5, 1.7), "se")}
OVEN_SPOT = (2.4, 2.4)
PASS_SPOT = (3.4, 6.4)
CRATES_SPOT = (7.6, 2.0)


@dataclass
class Actor:
    character: str
    position: tuple
    direction: str = "se"
    action: str = "idle"
    action_until: float = 0.0
    plan: deque = field(default_factory=deque)
    destination: tuple | None = None
    speech: str | None = None
    speech_until: float = 0.0
    carrying: str | None = None
    gone: bool = False
    order_id: str | None = None
    trait: str | None = None
    arrived: int = 0
    state: str | None = None
    slot: tuple | None = None

    def say(self, text, now, seconds=None):
        if text:
            self.speech = text
            self.speech_until = now + (seconds or min(6.0, 2.2 + 0.045 * len(text)))

    def do(self, action, now, seconds=2.0, direction=None):
        """Drop any plan and perform ``action`` right where the actor stands."""
        self.plan.clear()
        self.destination = None
        self.action = action
        self.action_until = now + seconds
        if direction:
            self.direction = direction

    def schedule(self, *steps):
        """Replace the plan: ``("go", point)``, ``("act", action, seconds, direction)``, ``("carry", item)``, ``("gone",)``."""
        self.plan = deque(steps)
        self.destination = None
        self.action = "idle"

    def tick(self, dt, now):
        if self.speech is not None and now >= self.speech_until:
            self.speech = None
        if self.destination is not None:
            self.position, direction, arrived = step_towards(self.position, self.destination, WALK_SPEED * dt)
            if direction:
                self.direction = direction
            self.action = "carry" if self.carrying and self.direction in ("se", "sw") else "walk"
            if not arrived:
                return
            self.destination = None
            self.action = "idle"
        elif self.action != "idle":
            if now < self.action_until:
                return
            self.action = "idle"
        while self.plan:
            step = self.plan.popleft()
            if step[0] == "go":
                self.destination = step[1]
                return
            if step[0] == "act":
                _, self.action, seconds, direction = step
                self.action_until = now + seconds
                if direction:
                    self.direction = direction
                return
            if step[0] == "carry":
                self.carrying = step[1]
            elif step[0] == "gone":
                self.gone = True
                return


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
    def __init__(self):
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
        self.services = {name: ServiceView() for name in SERVICES}
        self.seen = {name: set() for name in SERVICES}
        self.actors = {name: Actor(name, home, direction) for name, (home, direction) in HOMES.items()}
        self.customers = {}
        self.ticker = deque(maxlen=14)
        self.resyncs = 0
        self._arrivals = 0

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

    def everyone(self):
        return [*self.actors.values(), *self.customers.values()]

    # ------------------------------------------------------------------ updates

    def apply(self, update):
        if isinstance(update, Status):
            view = self.services[update.service]
            if (view.state, view.detail) != (update.state, update.detail):
                self._note(update.service, update.state, update.detail)
            view.state, view.detail = update.state, update.detail
        elif isinstance(update, Resync):
            self.resyncs += 1
            self._note("feed", "RESYNC", "updates were dropped; taking fresh snapshots")
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
        elif service == "inventory":
            self.stock = dict(data["items"])
            self.stock_version = data["stockVersion"]
            self.stock_uncertain = False
            self.reservations = len(data["reservations"])
            self.faults = data["faults"]
        self._reconcile_customers()

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
        self._project(service, event)
        self._react(service, event)
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

    # ------------------------------------------------------------------ reactions

    def _react(self, service, event):
        now, kind, payload = self.time, event["type"], event["payload"]
        seed = f"{event['instanceId']}:{event['sequence']}"
        if service == "order":
            self._react_order(kind, payload, event.get("orderId"), seed, now)
        elif service == "kitchen":
            self._react_kitchen(kind, payload, event.get("orderId"), seed, now)
        elif service == "inventory":
            self._react_inventory(kind, payload, event.get("orderId"), seed, now)

    def _words(self, order):
        order = order or {}
        customer = order.get("customer") or {}
        return {"pizza": order.get("pizzaType"), "quantity": order.get("quantity"), "short_order": short(order.get("orderId")),
                "name": customer.get("appearance", "customer_").replace("customer_", "").title() or None}

    def _react_order(self, kind, payload, order_id, seed, now):
        cashier = self.actors["cashier"]
        order = payload if kind.startswith("ORDER_") else self.orders.get(order_id)
        trait = ((order or {}).get("customer") or {}).get("trait", "patient")
        words = self._words(order)
        cashier.say(dialogue.cashier_line(kind, payload, trait, seed, **words), now)
        customer = self.customers.get(order_id)
        if kind == "ORDER_RECEIVED":
            customer = self._spawn_customer(order)
            cashier.do("talk", now, 1.6, "se")
        elif kind == "ORDER_CHECKING":
            cashier.do("work", now, 1.5, "sw")
        elif kind == "ORDER_RECONCILING":
            cashier.do("talk", now, 2.0, "sw")
        elif kind == "ORDER_READY":
            cashier.do("carry", now, 2.0, "se")
            self._dismiss(customer, "READY")
        elif kind == "ORDER_FAILED":
            cashier.do("frustrated", now, 2.2, "se")
            self._dismiss(customer, "FAILED")
        elif kind == "ORDER_UNKNOWN":
            cashier.do("talk", now, 2.0, "se")
            self._seat(customer)
        elif kind in ("DEPENDENCY_TIMEOUT", "DEPENDENCY_UNAVAILABLE"):
            cashier.do("frustrated", now, 1.8, "sw")
        if customer is not None and kind.startswith("ORDER_"):
            customer.say(dialogue.customer_line(kind, customer.trait, seed, **words), now)

    def _react_kitchen(self, kind, payload, order_id, seed, now):
        chef, worker = self.actors["chef"], self.actors["stock_worker"]
        job = payload if kind.startswith("COOK_") else self.jobs.get(order_id)
        words = {"pizza": (job or {}).get("pizzaType"), "quantity": (job or {}).get("quantity")}
        chef.say(dialogue.chef_line(kind, payload, self.mood_label, seed, **words), now)
        home, home_direction = HOMES["chef"]
        if kind == "COOK_CHECKING":
            chef.do("talk", now, 1.4, "sw")
        elif kind == "COOK_STARTED":
            chef.schedule(("go", OVEN_SPOT), ("act", "work", 90.0, "sw"))
        elif kind == "COOK_COMPLETED":
            chef.schedule(("carry", "pizza_box"), ("go", PASS_SPOT), ("act", "talk", 1.0, "se"), ("carry", None),
                          ("go", home), ("act", "idle", 0.0, home_direction))
        elif kind in ("COOK_FAILED", "COOK_UNKNOWN"):
            chef.do("frustrated", now, 2.5, "sw")
        elif kind == "COOK_RECONCILING":
            chef.do("talk", now, 2.0, "sw")
        elif kind == "RELATIONSHIP_CHANGED":
            chef.do("frustrated", now, 3.0, "se")
            worker.do("talk", now, 2.0, "sw")
        elif kind in ("DEPENDENCY_TIMEOUT", "DEPENDENCY_UNAVAILABLE"):
            chef.do("frustrated", now, 1.8, "se")

    def _react_inventory(self, kind, payload, order_id, seed, now):
        worker = self.actors["stock_worker"]
        words = self._words(self.orders.get(order_id))
        worker.say(dialogue.stock_worker_line(kind, payload, seed, **words), now)
        home, home_direction = HOMES["stock_worker"]
        if kind == "STOCK_READ":
            worker.do("talk", now, 1.2, "se")
        elif kind == "RESERVATION_SUCCEEDED":
            worker.schedule(("go", CRATES_SPOT), ("act", "carry", 0.8, "sw"), ("go", home), ("act", "work", 1.0, home_direction))
        elif kind == "RESERVATION_REJECTED":
            worker.do("frustrated", now, 2.0, "sw")
        elif kind == "STOCK_RESTOCKED":
            worker.do("carry", now, 2.0, "sw")
        elif kind == "FAULT_CONFIG_CHANGED":
            worker.do("talk", now, 2.0, "se")

    # ------------------------------------------------------------------ customers

    def _spawn_customer(self, order):
        order_id = order["orderId"]
        if order_id in self.customers:
            return self.customers[order_id]
        self._arrivals += 1
        customer = Actor(order["customer"]["appearance"], ENTRANCE, "nw", order_id=order_id, trait=order["customer"]["trait"],
                         arrived=self._arrivals, state="waiting")
        self.customers[order_id] = customer
        self._seat_waiting()
        return customer

    def _seat_waiting(self):
        waiting = sorted((customer for customer in self.customers.values() if customer.state == "waiting"), key=lambda c: c.arrived)
        for index, customer in enumerate(waiting):
            if index < len(WAIT_SLOTS):
                slot = WAIT_SLOTS[index]
            else:
                slot = (OVERFLOW_SPOT[0], OVERFLOW_SPOT[1] + 0.35 * (index - len(WAIT_SLOTS)))
            if customer.slot != slot:
                customer.slot = slot
                customer.schedule(("go", slot), ("act", "idle", 0.0, "sw"))

    def _dismiss(self, customer, outcome):
        if customer is None or customer.state == "leaving":
            return
        customer.state = "leaving"
        if outcome == "READY":
            customer.schedule(("go", WAIT_SLOTS[0]), ("act", "talk", 0.8, "sw"), ("carry", "pizza_box"), ("go", ENTRANCE), ("gone",))
        else:
            customer.schedule(("act", "frustrated", 2.4, "sw"), ("go", ENTRANCE), ("gone",))
        self._seat_waiting()

    def _seat(self, customer):
        if customer is None or customer.state in ("seated", "leaving"):
            return
        customer.state = "seated"
        seat = TABLE_SEATS[customer.arrived % len(TABLE_SEATS)]
        customer.slot = None
        customer.schedule(("go", seat), ("act", "talk", 1.5, "se"))
        self._seat_waiting()

    def _reconcile_customers(self):
        for order_id, order in self.orders.items():
            status = order["status"]
            customer = self.customers.get(order_id)
            if status in TERMINAL:
                self._dismiss(customer, status)
            elif customer is None:
                customer = self._spawn_customer(order)
            if status == "UNKNOWN":
                self._seat(customer)
        for order_id, customer in self.customers.items():
            if order_id not in self.orders:
                self._dismiss(customer, "GONE")

    # ------------------------------------------------------------------ time

    def tick(self, dt):
        self.time += dt
        for actor in self.actors.values():
            actor.tick(dt, self.time)
        departed = False
        for order_id, customer in list(self.customers.items()):
            customer.tick(dt, self.time)
            if customer.gone:
                del self.customers[order_id]
                departed = True
        if departed:
            self._seat_waiting()

    # ------------------------------------------------------------------ ticker

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
        self.ticker.append({"time": self.time, "service": service, "type": kind, "detail": detail, "orderId": event.get("orderId")})
