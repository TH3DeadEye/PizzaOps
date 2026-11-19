"""Who walks in, when, and what they order. Pure and seeded: no network, no clock.

Arrivals are a Poisson process whose rate can rise during a rush. Candidates are drawn at the peak rate and each
is kept with probability ``rate(t) / peak`` (thinning), so one seed always produces the same customers.
"""

import uuid
from dataclasses import dataclass

APPEARANCES = ("customer_amber", "customer_lavender", "customer_teal")
TRAITS = ("patient", "impatient", "chatty")
QUANTITY_WEIGHTS = {1: 0.62, 2: 0.30, 3: 0.08}


def seeded_uuid(rng):
    """A version-4 UUID drawn from ``rng``, so a seed reproduces request, customer and command IDs."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def choose(rng, weights):
    options = list(weights)
    return rng.choices(options, weights=[weights[option] for option in options])[0]


def order_body(rng, pizza, quantity, *, appearance=None, trait=None):
    """A complete ``POST /orders`` body for one customer."""
    return {"requestId": seeded_uuid(rng),
            "customer": {"id": seeded_uuid(rng), "appearance": appearance or rng.choice(APPEARANCES),
                         "trait": trait or rng.choice(TRAITS)},
            "pizzaType": pizza, "quantity": quantity}


@dataclass(frozen=True)
class Rate:
    """Arrivals per second: ``base`` everywhere, multiplied inside each ``(start, end, factor)`` rush window."""

    base: float
    rushes: tuple = ()

    def at(self, t):
        factor = 1.0
        for start, end, multiplier in self.rushes:
            if start <= t < end:
                factor *= multiplier
        return self.base * factor

    @property
    def peak(self):
        factor = 1.0
        for _, _, multiplier in self.rushes:
            factor *= max(1.0, multiplier)
        return self.base * factor


def arrivals(rng, start, end, rate, menu):
    """``(at, body)`` for every customer who walks in during ``[start, end)``, in time order."""
    peak = rate.peak
    found = []
    if peak <= 0:
        return found
    t = start
    while True:
        t += rng.expovariate(peak)
        if t >= end:
            return found
        if rng.random() * peak <= rate.at(t):
            found.append((t, order_body(rng, choose(rng, menu), choose(rng, QUANTITY_WEIGHTS))))
