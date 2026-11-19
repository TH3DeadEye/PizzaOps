"""The stage: who walks where, says what and carries which box. Pure Python: no pygame, no network.

The world projects what the services report; the stage acts it out. To look believable the acting may run a few
seconds behind the truth: a customer whose order is READY waits until the chef has set the box on the counter. It
never changes an outcome. Only a READY order is ever picked up, a FAILED order never produces a box, and staff
hurry when they fall behind instead of drifting further away from the truth. Random choices are seeded by event
identity or by the stage's own generator, so a replay plays the same scene.

Each actor works through a queue of beats (short step lists). Events append beats rather than replacing plans:

    ("go", point)                      walk a four-neighbour route      ("act", action, seconds, direction)
    ("work_until", condition, dir, s)  work until a condition holds     ("wait", condition, seconds)
    ("carry", sprite or None)          ("face", direction)              ("fade", alpha change per second)
    ("drop", order) / ("take", order)  hand a box over at the counter   ("thank", order)
    ("say", text)  ("emote", icon, s)  ("cue", name)                    ("gone",)

The stage also emits cues (sound and effect hints with a map position) for the renderer to collect.
"""

import random
from collections import deque
from dataclasses import dataclass, field

from viewer import dialogue
from viewer.geometry import step_towards

STAFF_WALK = 2.2           # tiles per second
CUSTOMER_WALK = 1.6
HANDOFF_WAIT_S = 8.0       # longest a READY customer waits for the chef to bring the box
IMPORTANT_S = 6.0          # how long an important line may wait for its speaker
CHATTER_S = 1.5            # small talk is dropped sooner
REACTION_S = 4.0           # a gesture that cannot start by then is dropped
BUSY_S = 4.0               # with more queued work than this, the chef argues from where he stands
HURRY_AFTER_S = 2.0        # a finished pizza waiting this long to be carried out makes staff walk faster
RUSH_AFTER_S = 6.0         # ...and beyond this they rush and skip gestures
MAX_SPEAKERS = 5
STAFF_FOR = {"order": "cashier", "kitchen": "chef", "inventory": "stock_worker"}
BOX_SLOTS = ((-0.16, -0.08), (0.12, 0.02), (-0.04, 0.18))
DONE = {"COOKED", "FAILED", "UNKNOWN"}


def line_seconds(text):
    return min(6.0, 2.2 + 0.045 * len(text))


def words_for(order):
    order = order or {}
    return {"pizza": order.get("pizzaType"), "quantity": order.get("quantity"),
            "name": dialogue.customer_name(order.get("customer"))}


def distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


@dataclass
class Beat:
    """``task`` beats always play; ``react`` beats are dropped if they cannot start before ``expires``; ``idle``
    beats give way to anything else. An ``urgent`` beat (carrying out a finished pizza) sets the actor's pace:
    the longer it waits to start, the faster everyone ahead of it is played."""

    steps: list
    kind: str = "task"
    order_id: str | None = None
    expires: float | None = None
    created: float = 0.0
    urgent: bool = False


@dataclass
class Line:
    start: float
    expires: float
    actor: "Actor"
    text: str


@dataclass
class Ticket:
    order_id: str
    box: str = "none"          # none | counter | taken | wasted
    cooked: bool = False       # Kitchen reported a final job status
    failed: bool = False       # Order reported FAILED: no box may ever be handed over
    dropped_at: float | None = None
    taken_at: float | None = None
    lines: dict = field(default_factory=dict)
    closed_at: float | None = None


@dataclass
class Prop:
    sprite: str
    position: tuple
    elevation: int
    order_id: str
    depth_bias: float = 0.03  # sorts just in front of the counter it rests on, whatever its small offset
    alpha: float = 1.0
    fading: float = 0.0


@dataclass
class Actor:
    character: str
    position: tuple
    direction: str = "se"
    action: str = "idle"
    action_until: float = 0.0
    plan: deque = field(default_factory=deque)
    beats: deque = field(default_factory=deque)
    beat: Beat | None = None
    destination: tuple | None = None
    waiting: tuple | None = None
    speech: str | None = None
    speech_since: float = 0.0
    speech_until: float = 0.0
    emote: str | None = None
    emote_until: float = 0.0
    carrying: str | None = None
    gone: bool = False
    alpha: float = 1.0
    fading: float = 0.0
    walk_speed: float = CUSTOMER_WALK
    rate: float = 1.0
    order_id: str | None = None
    trait: str | None = None
    arrived: int = 0
    arrived_at: float = 0.0
    state: str | None = None
    slot: tuple | None = None
    idle_since: float | None = None
    idle_gap: float = 8.0
    layout: object | None = field(default=None, repr=False)

    @property
    def busy(self):
        """Doing or holding real work (ambient idling does not count)."""
        return bool(self.beats) or (self.beat is not None and self.beat.kind != "idle")

    @property
    def moving(self):
        return self.destination is not None

    def say(self, text, now, seconds=None):
        if text:
            self.speech = text
            self.speech_since = now
            self.speech_until = now + (seconds or line_seconds(text))

    def queue(self, beat):
        if self.beat is not None and self.beat.kind == "idle":
            self.interrupt()
        self.beats.append(beat)

    def schedule(self, *steps, kind="task"):
        """Replace everything this actor planned with one beat (customers changing their mind)."""
        self.interrupt()
        self.beats.clear()
        self.beats.append(Beat(list(steps), kind))

    def interrupt(self):
        self.plan.clear()
        self.beat = None
        self.destination = None
        self.waiting = None
        self.action = "idle"

    def cancel(self, order_id):
        self.beats = deque(beat for beat in self.beats if beat.order_id != order_id)
        if self.beat is not None and self.beat.order_id == order_id:
            self.interrupt()

    def tick(self, dt, now, stage):
        if self.speech is not None and now >= self.speech_until:
            self.speech = None
        if self.emote is not None and now >= self.emote_until:
            self.emote = None
        if self.fading:
            self.alpha = min(1.0, max(0.0, self.alpha + self.fading * dt))
            if self.alpha in (0.0, 1.0):
                self.fading = 0.0
        if self.destination is not None:
            self.position, direction, arrived = step_towards(self.position, self.destination, self.walk_speed * self.rate * dt)
            if direction:
                self.direction = direction
            self.action = "carry" if self.carrying and self.direction in ("se", "sw") else "walk"
            if not arrived:
                return
            self.destination = None
            self.action = "idle"
        elif self.waiting is not None:
            condition, give_up = self.waiting
            if not stage.met(condition) and now < give_up:
                return
            self.waiting = None
            self.action = "idle"
        elif self.action != "idle":
            if now < self.action_until:
                return
            self.action = "idle"
        self._advance(now, stage)

    def _next_beat(self, now):
        while self.beats:
            beat = self.beats.popleft()
            if beat.kind == "react" and beat.expires is not None and now > beat.expires:
                continue
            self.beat = beat
            self.plan = deque(beat.steps)
            return True
        self.beat = None
        return False

    def _advance(self, now, stage):
        while True:
            if not self.plan and not self._next_beat(now):
                return
            if not self.plan:
                continue
            step = self.plan.popleft()
            kind = step[0]
            if kind in ("go", "via"):
                if kind == "go" and self.layout is not None:
                    path = self.layout.route(self.position, step[1])
                    if not path:
                        continue
                    self.destination = path[0]
                    self.plan.extendleft(reversed([("via", point) for point in path[1:]]))
                else:
                    self.destination = step[1]
                return
            if kind == "act":
                _, self.action, seconds, direction = step
                self.action_until = now + seconds / self.rate
                if direction:
                    self.direction = direction
                return
            if kind == "work_until":
                _, condition, direction, limit = step
                if stage.met(condition):
                    self.action, self.action_until = "work", now + 0.5 / self.rate
                else:
                    self.action, self.waiting = "work", (condition, now + limit)
                if direction:
                    self.direction = direction
                return
            if kind == "wait":
                if not stage.met(step[1]):
                    self.waiting = (step[1], now + step[2])
                    return
            elif kind == "carry":
                self.carrying = step[1]
            elif kind == "face":
                self.direction = step[1]
            elif kind == "fade":
                self.fading = step[1]
            elif kind == "gone":
                self.gone = True
                self.plan.clear()
                self.beats.clear()
                return
            else:
                stage.perform(self, step, now)


class Stage:
    def __init__(self, layout):
        self.layout = layout
        marks = layout.landmarks
        self.entrance = tuple(marks["entrance"])
        self.wait_slots = list(map(tuple, marks["wait_slots"]))
        self.overflow = tuple(marks["overflow"])
        self.table_seats = list(map(tuple, marks["table_seats"]))
        self.homes = {name: (tuple(home), direction) for name, (home, direction) in marks["homes"].items()}
        self.oven_spot, self.pass_spot, self.crates_spot = (tuple(marks[key]) for key in ("oven", "pass", "crates"))
        self.prep_spot = tuple(marks.get("prep", self.homes["chef"][0]))
        self.pickup_spots = [tuple(point) for point in marks.get("pickup", [self.wait_slots[0]])]
        self.pass_counter = tuple(marks.get("pass_counter", self.pass_spot))
        self.counter_elevation = marks.get("counter_elevation", 35)
        self.shelf_spot = tuple(marks.get("shelf", self.crates_spot))
        standoff = marks.get("standoff", {})
        self.standoff = {name: tuple(standoff.get(name, self.homes[name][0])) for name in ("chef", "stock_worker")}
        self.actors = {name: Actor(name, home, direction, walk_speed=STAFF_WALK, layout=layout)
                       for name, (home, direction) in self.homes.items()}
        self.customers = {}
        self.tickets = {}
        self.props = {}
        self.lines = []
        self.cues = []
        self.rng = random.Random("pizzaops-stage")
        self._arrivals = 0
        self._pickups = 0

    # ------------------------------------------------------------------ queries

    def everyone(self):
        return [*self.actors.values(), *self.customers.values()]

    def met(self, condition):
        kind = condition[0]
        if kind == "cooked":
            ticket = self.tickets.get(condition[1])
            return ticket is not None and ticket.cooked
        if kind == "box":
            ticket = self.tickets.get(condition[1])
            return ticket is not None and ticket.box == "counter"
        if kind == "near":
            actor = self.actors.get(condition[1])
            return actor is not None and actor.destination is None and distance(actor.position, condition[2]) < 0.05
        return True

    def backlog(self, actor, now):
        """Seconds of queued real work, estimated from walking distances and action lengths."""
        seconds, here = 0.0, actor.position
        if actor.action not in ("idle", "walk", "carry") and actor.waiting is None:
            seconds += max(0.0, actor.action_until - now) * actor.rate
        steps = list(actor.plan) if actor.beat is not None and actor.beat.kind != "idle" else []
        steps += [step for beat in actor.beats if beat.kind != "idle" for step in beat.steps]
        if actor.destination is not None:
            seconds += distance(here, actor.destination) / actor.walk_speed
            here = actor.destination
        for step in steps:
            if step[0] in ("go", "via"):
                seconds += distance(here, step[1]) / actor.walk_speed
                here = step[1]
            elif step[0] == "act":
                seconds += step[2]
            elif step[0] == "work_until":
                seconds += 0.5
        return seconds

    def ticket(self, order_id):
        if order_id not in self.tickets:
            self.tickets[order_id] = Ticket(order_id)
        return self.tickets[order_id]

    def take_cues(self):
        cues, self.cues = self.cues, []
        return cues

    # ------------------------------------------------------------------ speech and cues

    def say(self, actor, text, now, *, delay=0.0, ttl=IMPORTANT_S, urgent=False):
        """Queue a line: one speaker says one line at a time, in order, and stale lines are dropped.

        An ``urgent`` line belongs to a staged moment (setting a box down, an accusation at the door): it replaces
        whatever small talk the speaker had pending and is said right away.
        """
        if not text or actor is None or actor.gone:
            return
        if urgent:
            self.lines = [line for line in self.lines if line.actor is not actor]
            actor.say(text, now)
            return
        start = now + delay
        if actor.speech is not None:
            start = max(start, actor.speech_until)
        for line in self.lines:
            if line.actor is actor:
                start = max(start, line.start + line_seconds(line.text))
        self.lines.append(Line(start, now + delay + ttl, actor, text))
        self._speak(now)

    def _speak(self, now):
        speaking = sum(1 for actor in self.everyone() if actor.speech is not None and now < actor.speech_until)
        pending = []
        for line in sorted(self.lines, key=lambda line: line.start):
            if line.actor.gone or now > line.expires:
                continue
            free = line.actor.speech is None or now >= line.actor.speech_until
            if line.start <= now and free and speaking < MAX_SPEAKERS:
                line.actor.say(line.text, now)
                speaking += 1
                continue
            pending.append(line)
        self.lines = pending

    def cue(self, name, position, now):
        self.cues.append((name, tuple(position), now))

    def gesture(self, actor, action, seconds, direction, now):
        """A small reaction, only when the actor is not in the middle of real work."""
        if actor is not None and not actor.gone and not actor.busy and not actor.moving:
            actor.queue(Beat([("act", action, seconds, direction)], "react", expires=now + REACTION_S))

    # ------------------------------------------------------------------ steps performed by actors

    def perform(self, actor, step, now):
        kind = step[0]
        if kind == "say":
            self.say(actor, step[1], now, urgent=True)
        elif kind == "emote":
            actor.emote, actor.emote_until = step[1], now + step[2]
        elif kind == "cue":
            self.cue(step[1], actor.position, now)
        elif kind == "drop":
            self._drop(actor, step[1], now)
        elif kind == "take":
            self._take(actor, step[1], now)
        elif kind == "thank":
            self.say(actor, self.ticket(step[1]).lines.get("customer"), now, urgent=True)

    def _drop(self, chef, order_id, now):
        ticket = self.ticket(order_id)
        if ticket.failed or ticket.box != "none":
            self.cue("bin", chef.position, now)
            return
        ticket.box, ticket.dropped_at = "counter", now
        on_counter = sum(1 for prop in self.props.values() if prop.fading >= 0)
        dx, dy = BOX_SLOTS[on_counter % len(BOX_SLOTS)]
        lift = 7 * (on_counter // len(BOX_SLOTS))
        self.props[order_id] = Prop("pizza_box", (self.pass_counter[0] + dx, self.pass_counter[1] + dy),
                                    self.counter_elevation + lift, order_id, depth_bias=0.03 - dx - dy + 0.001 * on_counter)
        self.cue("bell", self.pass_counter, now)
        self.say(chef, ticket.lines.get("chef"), now, urgent=True)

    def _take(self, customer, order_id, now):
        ticket = self.ticket(order_id)
        if ticket.failed:
            return
        ticket.box, ticket.taken_at = "taken", now
        self.props.pop(order_id, None)
        customer.carrying = "pizza_box"
        self.cue("take", customer.position, now)
        self.say(self.actors["cashier"], ticket.lines.get("cashier"), now, urgent=True)

    # ------------------------------------------------------------------ events

    def react(self, service, event, world):
        seed = f"{event['instanceId']}:{event['sequence']}"
        handler = {"order": self._on_order, "kitchen": self._on_kitchen, "inventory": self._on_inventory}[service]
        handler(event["type"], event["payload"], event.get("orderId"), seed, world.time, world)

    def _on_order(self, kind, payload, order_id, seed, now, world):
        cashier = self.actors["cashier"]
        order = payload if kind.startswith("ORDER_") else world.orders.get(order_id)
        words = words_for(order)
        trait = ((order or {}).get("customer") or {}).get("trait", "patient")
        cashier_line = dialogue.cashier_line(kind, payload, trait, seed, **words)
        customer_line = dialogue.customer_line(kind, trait, seed, **words) if kind.startswith("ORDER_") else None
        customer = self.customers.get(order_id)
        if kind == "ORDER_RECEIVED":
            customer = self.spawn(order, now)
            self.say(customer, customer_line, now)
            self.say(cashier, cashier_line, now, delay=1.0)
            self.gesture(cashier, "talk", 1.4, "se", now)
            self.cue("register", cashier.position, now + 1.0)
        elif kind == "ORDER_READY":
            ticket = self.ticket(order_id)
            ticket.lines.update(cashier=cashier_line, customer=customer_line)
            self.collect(customer, now)
        elif kind == "ORDER_FAILED":
            self.say(cashier, cashier_line, now)
            self.gesture(cashier, "frustrated", 1.8, "se", now)
            if customer is not None:
                self.say(customer, customer_line, now, delay=1.2)
            self.waste(order_id, now)
            self.dismiss(customer, now, upset=True)
            self.cue("fail", cashier.position, now)
        elif kind == "ORDER_UNKNOWN":
            self.say(cashier, cashier_line, now)
            self.gesture(cashier, "talk", 2.0, "se", now)
            if customer is not None:
                self.say(customer, customer_line, now, delay=1.2)
            self.seat(customer, now)
        else:
            chatter = kind in ("ORDER_CHECKING", "ORDER_COOKING")
            self.say(cashier, cashier_line, now, ttl=CHATTER_S if chatter else IMPORTANT_S)
            if customer is not None and customer.state == "waiting":
                self.say(customer, customer_line, now, delay=1.0, ttl=CHATTER_S)
            if kind == "ORDER_CHECKING":
                self.gesture(cashier, "work", 1.2, "sw", now)
                self.cue("ticket", cashier.position, now)
            elif kind == "ORDER_RECONCILING":
                self.gesture(cashier, "talk", 1.6, "sw", now)
            elif kind.startswith("DEPENDENCY_"):
                self.gesture(cashier, "frustrated", 1.6, "sw", now)

    def _on_kitchen(self, kind, payload, order_id, seed, now, world):
        chef = self.actors["chef"]
        job = payload if kind.startswith("COOK_") else world.jobs.get(order_id)
        words = {"pizza": (job or {}).get("pizzaType"), "quantity": (job or {}).get("quantity")}
        line = dialogue.chef_line(kind, payload, world.mood_label, seed, **words)
        if kind == "COOK_STARTED":
            self.say(chef, line, now, ttl=CHATTER_S)
            self.cue("oven", self.oven_spot, now)
            chef.queue(Beat([("go", self.prep_spot), ("act", "work", 1.0, "sw"), ("go", self.oven_spot),
                             ("work_until", ("cooked", order_id), "sw", 60.0)], "task", order_id, created=now))
        elif kind == "COOK_COMPLETED":
            ticket = self.ticket(order_id)
            ticket.cooked = True
            ticket.lines["chef"] = line
            # Oven to pass and back to the prep counter fits inside a 10 s bake, so one cook keeps pace.
            chef.queue(Beat([("go", self.oven_spot), ("act", "work", 0.5, "sw"), ("carry", "pizza_box"),
                             ("go", self.pass_spot), ("drop", order_id), ("carry", None)],
                            "task", order_id, created=now, urgent=True))
        elif kind in ("COOK_FAILED", "COOK_UNKNOWN"):
            self.ticket(order_id).cooked = True
            chef.cancel(order_id)
            self.say(chef, line, now)
            self.gesture(chef, "frustrated", 2.0, "sw", now)
        elif kind == "RELATIONSHIP_CHANGED":
            self.confront(line, seed, now)
        elif kind == "STOCK_CHECKED":
            self.say(chef, line, now, ttl=IMPORTANT_S if not payload.get("sufficient") else CHATTER_S)
        elif kind == "COOK_CHECKING":
            self.say(chef, line, now, ttl=CHATTER_S)
            self.gesture(chef, "talk", 1.0, "sw", now)
        elif kind == "COOK_RECONCILING":
            self.say(chef, line, now)
            self.gesture(chef, "talk", 1.6, "sw", now)
        elif kind.startswith("DEPENDENCY_"):
            self.say(chef, line, now)
            self.gesture(chef, "frustrated", 1.6, "se", now)

    def _on_inventory(self, kind, payload, order_id, seed, now, world):
        worker = self.actors["stock_worker"]
        line = dialogue.stock_worker_line(kind, payload, seed, **words_for(world.orders.get(order_id)))
        if kind == "STOCK_READ":
            cached = payload.get("source") == "CACHED"
            self.say(worker, line, now, ttl=IMPORTANT_S if cached else CHATTER_S)
            self.gesture(worker, "talk", 1.0, "se", now)
        elif kind == "RESERVATION_SUCCEEDED":
            self.say(worker, line, now, ttl=CHATTER_S)
            if not worker.busy:
                worker.queue(Beat([("go", self.crates_spot), ("act", "carry", 0.8, "sw")], "react", expires=now + REACTION_S))
        elif kind == "RESERVATION_REJECTED":
            self.say(worker, line, now)
            self.gesture(worker, "frustrated", 1.6, "sw", now)
        elif kind == "STOCK_RESTOCKED":
            self.say(worker, line, now)
            worker.queue(Beat([("go", self.crates_spot), ("cue", "restock"), ("act", "carry", 1.6, "sw")],
                              "react", expires=now + 2 * REACTION_S))
        elif kind == "FAULT_CONFIG_CHANGED":
            self.say(worker, line, now)
            self.gesture(worker, "talk", 1.6, "se", now)

    def confront(self, line, seed, now):
        """The chef caught the stock sheet lying: march to the stockroom door and have it out."""
        chef, worker = self.actors["chef"], self.actors["stock_worker"]
        reply = dialogue.stock_worker_line("STANDOFF", {}, seed)
        self.cue("sting", chef.position, now)
        if self.backlog(chef, now) > BUSY_S:
            chef.emote, chef.emote_until = "angry", now + 2.5
            self.say(chef, line, now)
            self.say(worker, reply, now, delay=1.5)
            return
        here = self.standoff["chef"]
        chef.queue(Beat([("go", here), ("face", "se"), ("emote", "angry", 2.6), ("say", line), ("act", "frustrated", 2.4, "se")],
                        "react", expires=now + 6.0))
        worker.queue(Beat([("go", self.standoff["stock_worker"]), ("wait", ("near", "chef", here), 7.0),
                           ("act", "talk", 1.2, "sw"), ("say", reply), ("act", "talk", 2.0, "sw")],
                          "react", expires=now + 6.0))

    # ------------------------------------------------------------------ customers

    def spawn(self, order, now):
        order_id = order["orderId"]
        if order_id in self.customers:
            return self.customers[order_id]
        self._arrivals += 1
        customer = Actor(order["customer"]["appearance"], self.entrance, "nw", order_id=order_id,
                         trait=order["customer"]["trait"], arrived=self._arrivals, arrived_at=now, state="waiting",
                         alpha=0.0, fading=3.0, layout=self.layout)
        self.customers[order_id] = customer
        self.cue("door", self.entrance, now)
        self.seat_waiting()
        return customer

    def seat_waiting(self):
        waiting = sorted((customer for customer in self.customers.values() if customer.state == "waiting"),
                         key=lambda customer: customer.arrived)
        for index, customer in enumerate(waiting):
            # Overflow shares the lobby holding point instead of drifting outside the map.
            slot = self.wait_slots[index] if index < len(self.wait_slots) else self.overflow
            if customer.slot != slot:
                customer.slot = slot
                customer.schedule(("go", slot), ("act", "idle", 0.0, "sw"))

    def collect(self, customer, now):
        """A READY customer waits for the box on the counter (or the cashier fetches it), takes it and leaves."""
        if customer is None or customer.state in ("collecting", "leaving"):
            return
        customer.state, customer.slot = "collecting", None
        order_id = customer.order_id
        spot = self.pickup_spots[self._pickups % len(self.pickup_spots)]
        self._pickups += 1
        customer.schedule(("wait", ("box", order_id), HANDOFF_WAIT_S), ("go", spot), ("face", "se"), ("take", order_id),
                          ("act", "talk", 0.9, "se"), ("thank", order_id), ("go", self.entrance),
                          ("fade", -2.5), ("act", "idle", 0.4, None), ("gone",))
        self.seat_waiting()

    def dismiss(self, customer, now, *, upset):
        if customer is None or customer.state == "leaving":
            return
        customer.state, customer.slot = "leaving", None
        steps = [("act", "frustrated", 2.0, "sw")] if upset else []
        customer.schedule(*steps, ("go", self.entrance), ("fade", -2.5), ("act", "idle", 0.4, None), ("gone",))
        if upset:
            customer.emote, customer.emote_until = "warning", now + 2.0
        self.seat_waiting()

    def seat(self, customer, now):
        if customer is None or customer.state in ("seated", "collecting", "leaving"):
            return
        customer.state, customer.slot = "seated", None
        customer.emote, customer.emote_until = "unknown", now + 3.0
        customer.schedule(("go", self.table_seats[customer.arrived % len(self.table_seats)]), ("act", "talk", 1.5, "se"))
        self.seat_waiting()

    def waste(self, order_id, now):
        ticket = self.ticket(order_id)
        ticket.failed = True
        if ticket.box == "counter":
            ticket.box = "wasted"
            prop = self.props.get(order_id)
            if prop is not None:
                prop.fading = -2.0
                self.cue("bin", prop.position, now)

    def reconcile(self, world):
        """After a snapshot: make the cast match the orders that actually exist."""
        now = world.time
        for order_id, order in world.orders.items():
            status = order["status"]
            customer = self.customers.get(order_id)
            if status == "READY":
                self.ticket(order_id)
                self.collect(customer, now)
            elif status == "FAILED":
                self.waste(order_id, now)
                self.dismiss(customer, now, upset=True)
            elif customer is None:
                customer = self.spawn(order, now)
            if status == "UNKNOWN":
                self.seat(customer, now)
        for order_id, customer in self.customers.items():
            if order_id not in world.orders:
                self.dismiss(customer, now, upset=False)

    # ------------------------------------------------------------------ time

    def tick(self, dt, now, world):
        self._speak(now)
        for actor in self.actors.values():
            actor.rate = self._pace(actor, now)
            actor.tick(dt, now, self)
        departed = False
        for order_id, customer in list(self.customers.items()):
            customer.tick(dt, now, self)
            if customer.gone:
                del self.customers[order_id]
                self.ticket(order_id).closed_at = now
                departed = True
        if departed:
            self.seat_waiting()
        for order_id, prop in list(self.props.items()):
            if prop.fading:
                prop.alpha = max(0.0, prop.alpha + prop.fading * dt)
                if prop.alpha <= 0:
                    del self.props[order_id]
        self._ambient(now)
        if self.tickets and int(now) % 10 == 0:
            self._prune(now, world)

    def _pace(self, actor, now):
        """Normal pace, unless a finished pizza is waiting to be carried out: then hurry, or rush and skip gestures."""
        lag = max((now - beat.created for beat in actor.beats if beat.urgent), default=0.0)
        if lag > RUSH_AFTER_S:
            actor.beats = deque(beat for beat in actor.beats if beat.kind == "task")
            if actor.beat is not None and actor.beat.kind != "task":
                actor.interrupt()
            return 2.0
        return 1.5 if lag > HURRY_AFTER_S else 1.0

    def _ambient(self, now):
        for name, actor in self.actors.items():
            if actor.busy or actor.beat is not None or actor.moving or actor.waiting or actor.action != "idle":
                actor.idle_since = None
                continue
            if actor.idle_since is None:
                actor.idle_since, actor.idle_gap = now, self.rng.uniform(6.0, 14.0)
                continue
            home, direction = self.homes[name]
            if distance(actor.position, home) > 0.05:
                if now - actor.idle_since > 1.2:
                    actor.queue(Beat([("go", home), ("act", "idle", 0.0, direction)], "idle"))
                    actor.idle_since = None
            elif now - actor.idle_since > actor.idle_gap:
                actor.queue(Beat(self._pastime(name, direction), "idle"))
                actor.idle_since = None
        for customer in self.customers.values():
            if customer.state != "waiting" or customer.beat is not None or customer.moving or customer.action != "idle":
                customer.idle_since = None
                continue
            if customer.idle_since is None:
                customer.idle_since, customer.idle_gap = now, self.rng.uniform(9.0, 18.0)
                continue
            if now - customer.idle_since < customer.idle_gap:
                continue
            customer.idle_since = None
            if customer.trait == "impatient" and now - customer.arrived_at > 25:
                customer.queue(Beat([("act", "frustrated", 1.2, "sw")], "idle"))
                self.say(customer, dialogue.customer_line("ORDER_COOKING", "impatient", f"{customer.order_id}:{int(now)}"),
                         now, ttl=CHATTER_S)
            elif customer.trait == "chatty":
                customer.queue(Beat([("act", "talk", 1.2, "se")], "idle"))

    def _pastime(self, name, direction):
        if name == "chef":
            return [("act", "work", 2.2, "sw")]  # kneading at the prep counter
        if name == "stock_worker":
            return [("go", self.shelf_spot), ("act", "work", 2.0, "se")]
        return [("act", "work", 1.6, direction)]  # the cashier tidies tickets

    def _prune(self, now, world):
        for order_id, ticket in list(self.tickets.items()):
            if ticket.closed_at is not None and now - ticket.closed_at > 30 and order_id not in self.props:
                del self.tickets[order_id]
