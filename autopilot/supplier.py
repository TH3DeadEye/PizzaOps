"""The supplier: every so often it reads the real stock and tops up whatever is low, except what the director holds back.

Pure decision logic. ``autopilot/run.py`` makes the HTTP calls, always reading stock with ``authoritative=true`` so a
stale stock sheet can never fool the delivery truck.
"""

from dataclasses import dataclass, field

TARGET = {"DOUGH": 40, "SAUCE": 40, "CHEESE": 30, "PEPPERONI": 15, "BASIL": 15}
LOW = {"DOUGH": 12, "SAUCE": 12, "CHEESE": 9, "PEPPERONI": 5, "BASIL": 5}
INTERVAL_S = 20.0


@dataclass
class Supplier:
    target: dict = field(default_factory=lambda: dict(TARGET))
    low: dict = field(default_factory=lambda: dict(LOW))
    held: set = field(default_factory=set)

    def delivery_for(self, stock):
        """The ``items`` of a restock for an authoritative ``stock`` reading; empty when nothing is needed."""
        return {name: self.target[name] - quantity for name, quantity in sorted(stock.items())
                if name in self.target and name not in self.held
                and quantity < self.low.get(name, 0) and quantity < self.target[name]}
