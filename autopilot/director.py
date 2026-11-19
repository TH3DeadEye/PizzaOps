"""Scenarios: the script of a shift. Each is a base arrival rate, a menu mix and a list of timed cues.

The director only uses doors any operator has: Inventory's fault settings, the kitchen's reconcile command, the
supplier's schedule and who walks in. It never reaches into a service's memory and never decides an outcome; a
"stale stock" cue only switches the stockkeeper to an old sheet, and what that causes is up to the services.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cue:
    at: float
    kind: str  # "faults" | "reconcile" | "hold" | "release" | "order" | "rush"
    args: dict = field(default_factory=dict)


def faults(at, *, stale=False, delay_ms=0, unavailable=False):
    """A complete fault configuration (Inventory applies full updates, so every field is always sent)."""
    return Cue(at, "faults", {"staleStockEnabled": stale, "delayMs": delay_ms,
                              "failureMode": "UNAVAILABLE" if unavailable else "NONE", "delayPhase": "BEFORE_MUTATION"})


def reconcile(at):
    return Cue(at, "reconcile")


def hold(at, ingredient):
    return Cue(at, "hold", {"ingredient": ingredient})


def release(at, ingredient):
    return Cue(at, "release", {"ingredient": ingredient})


def walk_in(at, pizza, quantity, **customer):
    return Cue(at, "order", {"pizza": pizza, "quantity": quantity, **customer})


def rush(start, end, factor):
    return Cue(start, "rush", {"end": end, "factor": factor})


@dataclass(frozen=True)
class Scenario:
    name: str
    summary: str
    duration_s: float
    rate_per_s: float
    menu: dict
    cues: tuple = ()


CALM = Scenario(
    "calm", "An easy evening: steady customers, a punctual supplier, nothing goes wrong.",
    300, 1 / 16, {"CHEESE": 0.35, "PEPPERONI": 0.35, "MARINARA": 0.30},
    (walk_in(3, "MARINARA", 1, trait="patient"), reconcile(150)),
)

FRIDAY = Scenario(
    "friday", "Friday night: a stale stock sheet, a pepperoni shortage, a short outage, a rush and a slow storeroom.",
    300, 1 / 20, {"PEPPERONI": 0.45, "CHEESE": 0.35, "MARINARA": 0.20},
    (
        walk_in(3, "PEPPERONI", 1, trait="patient"),
        # The stockkeeper starts reading from a sheet frozen now; the pepperoni truck is late.
        faults(45, stale=True), hold(45, "PEPPERONI"),
        # A short outage while the queue is short, so only a customer or two is turned away.
        faults(70, stale=True, unavailable=True), faults(76, stale=True),
        # Two parties drain the pepperoni while the frozen sheet still says there is plenty.
        walk_in(90, "PEPPERONI", 6, trait="chatty"),
        walk_in(135, "PEPPERONI", 5, trait="impatient"),
        # Pepperoni lovers keep coming; each catch costs the sheet some trust until the chef counts for himself.
        walk_in(145, "PEPPERONI", 1, trait="chatty"), walk_in(158, "PEPPERONI", 2, trait="impatient"),
        walk_in(171, "PEPPERONI", 1, trait="patient"), walk_in(186, "PEPPERONI", 1, trait="impatient"),
        # Real counting resumes and the truck arrives; a huddle settles the chef.
        faults(200), release(200, "PEPPERONI"), reconcile(210),
        # The cinema lets out: a guaranteed wave of walk-ins on top of a busier random crowd.
        rush(225, 255, 2.0),
        walk_in(226, "CHEESE", 1, trait="impatient"), walk_in(228, "MARINARA", 2, trait="chatty"),
        walk_in(231, "PEPPERONI", 1, trait="impatient"), walk_in(235, "CHEESE", 2, trait="patient"),
        # A slow storeroom: replies take longer than the kitchen's 2 s attempt budget.
        faults(262, delay_ms=2500), faults(280), reconcile(285),
    ),
)

SCENARIOS = {scenario.name: scenario for scenario in (FRIDAY, CALM)}
