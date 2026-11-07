from uuid import UUID, uuid4

from services.inventory.errors import InventoryError


def read_trace(value):
    if value is None:
        return uuid4()
    try:
        if len(value) > 64:
            raise ValueError
        return UUID(value)
    except ValueError as error:
        raise InventoryError(400, "INVALID_REQUEST", "X-Trace-Id must be a UUID") from error
