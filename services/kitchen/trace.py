from uuid import UUID, uuid4

from services.kitchen.errors import KitchenError


def read_trace(value):
    if value is None:
        return uuid4()
    try:
        if len(value) > 64:
            raise ValueError
        return UUID(value)
    except ValueError as error:
        raise KitchenError(400, "INVALID_REQUEST", "X-Trace-Id must be a UUID") from error
