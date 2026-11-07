from services.kitchen.errors import KitchenError

RECIPES: dict[str, dict[str, int]] = {
    "CHEESE": {"DOUGH": 1, "SAUCE": 1, "CHEESE": 1},
    "PEPPERONI": {"DOUGH": 1, "SAUCE": 1, "CHEESE": 1, "PEPPERONI": 1},
    "MARINARA": {"DOUGH": 1, "SAUCE": 1, "BASIL": 1},
}


def recipe(pizza_type):
    try:
        return RECIPES[pizza_type]
    except KeyError:
        raise KitchenError(400, "UNKNOWN_PIZZA", "Unknown pizza type",
                           {"pizzaType": pizza_type, "known": sorted(RECIPES)}) from None


def expand(pizza_type, quantity):
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise KitchenError(400, "INVALID_REQUEST", "Quantity must be a positive integer")
    return {name: units * quantity for name, units in recipe(pizza_type).items()}


def shortfall(available, needed):
    return {name: quantity - available.get(name, 0) for name, quantity in needed.items() if available.get(name, 0) < quantity}


def sufficient(available, needed):
    return not shortfall(available, needed)
