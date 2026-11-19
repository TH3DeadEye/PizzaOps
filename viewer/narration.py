"""The story strip: what just happened, in one plain sentence with an icon and a tone.

Narration reports the truth as the services told it, the moment they told it; unlike the stage it never waits for
an animation. No IDs, status codes or event names reach the text (the F1 overlay keeps those for debugging).
Call ``narrate`` before the world applies the event, so a change can be described against what was true before it.
"""

from dataclasses import dataclass

from viewer.dialogue import customer_name, first_name


@dataclass(frozen=True)
class Story:
    text: str
    icon: str   # a sprite id from the pack
    tone: str   # "good" | "bad" | "warn" | "info"


PIZZA_ICONS = {"CHEESE": "pizza_cheese", "PEPPERONI": "pizza_pepperoni", "MARINARA": "pizza_raw"}
PLACES = {"order": "the front counter", "kitchen": "the kitchen", "inventory": "the stockroom"}
WHY = {
    "INVENTORY_UNAVAILABLE": "the stockroom was closed",
    "KITCHEN_UNAVAILABLE": "the kitchen didn't answer",
    "KITCHEN_QUEUE_FULL": "the kitchen was swamped",
    "QUEUE_FULL": "the kitchen was swamped",
    "COOK_DEADLINE_EXCEEDED": "it waited too long in the queue",
    "FULFILLMENT_UNCONFIRMED": "nobody can confirm it was made",
    "DEPENDENCY_STATE_LOST": "a restart wiped the kitchen's notes",
    "UNKNOWN_PIZZA": "that pizza isn't on the menu",
    "RECIPE_CONFIGURATION_ERROR": "the recipe card is wrong",
}
MOODS = {"CALM": ("Chef calmed down", "ui_mood_calm", "good"),
         "TENSE": ("Chef is getting tense", "ui_mood_busy", "warn"),
         "SUSPICIOUS": ("Chef stopped trusting the stock sheet", "ui_mood_stressed", "warn"),
         "FURIOUS": ("Chef is furious", "ui_mood_stressed", "bad")}


def pizza_words(order):
    order = order or {}
    pizza = str(order.get("pizzaType") or "pizza").lower()
    quantity = order.get("quantity") or 1
    return f"a {pizza} pizza" if quantity == 1 else f"{quantity} {pizza} pizzas"


def who(order):
    return customer_name((order or {}).get("customer")) or "A customer"


def missing_from(order):
    job_error = ((order.get("lastError") or {}).get("details") or {}).get("jobError") or {}
    return first_name((job_error.get("details") or {}).get("missing"), "ingredients")


def narrate(service, event, world):
    """A ``Story`` for this event, or None when it is not worth a line."""
    kind, payload = event["type"], event["payload"] or {}
    if service == "order":
        return _order(kind, payload, event)
    if service == "kitchen":
        return _kitchen(kind, payload, event, world)
    return _inventory(kind, payload, event, world)


def _order(kind, order, event):
    if kind == "ORDER_RECEIVED":
        return Story(f"{who(order)} ordered {pizza_words(order)}", PIZZA_ICONS.get(order.get("pizzaType"), "pizza_cheese"), "info")
    if kind == "ORDER_READY":
        many = (order.get("quantity") or 1) > 1
        return Story(f"{who(order)}'s {'pizzas are' if many else 'pizza is'} ready", "pizza_box", "good")
    if kind == "ORDER_FAILED":
        reason = order.get("failureReason")
        if reason == "INSUFFICIENT_STOCK":
            return Story(f"Out of {missing_from(order)}: {who(order)} left without a pizza", "ui_warning", "bad")
        return Story(f"{who(order)} left without a pizza: {WHY.get(reason, 'something went wrong')}", "ui_warning", "bad")
    if kind == "ORDER_UNKNOWN":
        return Story(f"{who(order)} waits at a table: {WHY.get(order.get('failureReason'), 'nobody knows what happened')}",
                     "ui_mood_busy", "warn")
    if kind == "ORDER_RECONCILING":
        return Story(f"The cashier is checking whether the kitchen got {who(order)}'s ticket", "ui_mood_busy", "warn")
    return _common(kind, event, "The cashier", "the kitchen")


def _kitchen(kind, payload, event, world):
    if kind == "COOK_STARTED":
        order = world.orders.get(event.get("orderId"))
        whose = f" for {who(order)}" if order else ""
        return Story(f"{pizza_words(payload).capitalize()}{whose} went into the oven", "pizza_raw", "info")
    if kind == "RELATIONSHIP_CHANGED":
        return Story(f"Chef caught the stock sheet lying about {first_name(payload.get('missing'))}", "ui_mood_stressed", "bad")
    if kind == "MOOD_CHANGED":
        before, after = (payload.get("before") or {}).get("mood"), (payload.get("after") or {}).get("mood")
        if after in MOODS and after != before:
            return Story(*MOODS[after])
        return None
    if kind == "STOCK_CHECKED" and payload.get("source") == "AUTHORITATIVE" and not payload.get("sufficient"):
        return Story(f"Chef counted the shelves himself: no {first_name(payload.get('missing'))}", "ui_mood_busy", "info")
    if kind == "COOK_RECONCILING":
        return Story("The kitchen is checking whether its stock request went through", "ui_mood_busy", "warn")
    return _common(kind, event, "The kitchen", "the stockroom")


def _inventory(kind, payload, event, world):
    if kind == "STOCK_RESTOCKED":
        added = payload.get("added") or {}
        items = ", ".join(f"{quantity} {name.lower()}" for name, quantity in sorted(added.items()))
        return Story(f"Delivery: {items}" if items else "A delivery arrived", "flour_sack", "good")
    if kind == "FAULT_CONFIG_CHANGED":
        return _faults(world.faults or {}, payload.get("faults") or {})
    return _common(kind, event, "", "")


def _faults(before, after):
    was_closed, closed = before.get("failureMode") == "UNAVAILABLE", after.get("failureMode") == "UNAVAILABLE"
    if closed and not was_closed:
        return Story("The stockroom closed its door", "ui_warning", "bad")
    if was_closed and not closed:
        return Story("The stockroom opened again", "ui_mood_calm", "good")
    if after.get("staleStockEnabled") and not before.get("staleStockEnabled"):
        return Story("The stockkeeper is counting from memory", "ui_warning", "warn")
    if before.get("staleStockEnabled") and not after.get("staleStockEnabled"):
        return Story("The stockkeeper is counting properly again", "ui_mood_calm", "good")
    if after.get("delayMs") and not before.get("delayMs"):
        return Story("The stockroom is slow to answer", "ui_warning", "warn")
    if before.get("delayMs") and not after.get("delayMs"):
        return Story("The stockroom is quick again", "ui_mood_calm", "good")
    return None


def _common(kind, event, caller, callee):
    if kind == "DEPENDENCY_TIMEOUT" and caller:
        return Story(f"{caller} gave up waiting on {callee}", "ui_warning", "warn")
    if kind == "DEPENDENCY_UNAVAILABLE" and caller:
        return Story(f"{caller} couldn't reach {callee}", "ui_warning", "warn")
    if kind in ("SERVICE_STARTED", "SERVICE_STOPPING"):
        place = PLACES.get(event.get("service"), "the restaurant")
        opened = kind == "SERVICE_STARTED"
        return Story(f"{place.capitalize()} {'opened' if opened else 'is closing'}", "ui_play" if opened else "ui_pause", "info")
    return None


def status_story(service, state, previous):
    """A line for a connection change seen by the viewer itself."""
    place = PLACES.get(service, service)
    if state == "DISCONNECTED" and previous in ("LIVE", "SYNCING"):
        return Story(f"Lost contact with {place}", "ui_warning", "bad")
    if state == "LIVE" and previous == "DISCONNECTED":
        return Story(f"{place.capitalize()} is back", "ui_mood_calm", "good")
    if state == "RUN_MISMATCH":
        return Story(f"{place.capitalize()} belongs to a different shift", "ui_warning", "bad")
    return None
