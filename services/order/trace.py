from uuid import UUID, uuid4

from services.order.errors import OrderError


def read_trace(value):
    if value is None:
        return uuid4()
    try:
        if len(value) > 64:
            raise ValueError
        return UUID(value)
    except ValueError as error:
        raise OrderError(400, "INVALID_REQUEST", "X-Trace-Id must be a UUID") from error
