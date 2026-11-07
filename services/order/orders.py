"""The order book: records, admission, legal state transitions, pagination and the event journal.

Order owns the truth about an order. READY is only ever written by a caller that observed Kitchen's COOKED; the book
itself only enforces that every status change follows ``TRANSITIONS`` and emits exactly one event per change.
"""

import asyncio
import base64
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from services.order.errors import OrderError
from services.order.kitchen_client import DependencyLost
from services.order.events import (
    CheckingEvent, CookingEvent, DependencyPayload, FailedEvent, Journal, ReadyEvent, ReceivedEvent, ReconcilingEvent,
    ServicePayload, StartedEvent, StoppingEvent, TimeoutEvent, UnavailableEvent, UnknownEvent,
)
from services.order.models import (
    Attempt, DependencyState, Order, OrderPage, OrderStatus, QueueState, Snapshot, TRANSITIONS,
)

STATUS_EVENTS = {
    OrderStatus.CHECKING: CheckingEvent,
    OrderStatus.COOKING: CookingEvent,
    OrderStatus.RECONCILING: ReconcilingEvent,
    OrderStatus.READY: ReadyEvent,
    OrderStatus.FAILED: FailedEvent,
    OrderStatus.UNKNOWN: UnknownEvent,
}


class IllegalTransition(Exception):
    def __init__(self, current, wanted):
        super().__init__(f"Order cannot move from {current} to {wanted}")
        self.current = current
        self.wanted = wanted


def encode_cursor(sequence):
    return base64.urlsafe_b64encode(str(sequence).encode()).decode()


def decode_cursor(cursor):
    try:
        sequence = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        if sequence < 1:
            raise ValueError
        return sequence
    except (ValueError, UnicodeDecodeError) as error:
        raise OrderError(400, "INVALID_CURSOR", "The list cursor is not valid") from error


class OrderBook:
    def __init__(self, settings, *, clock=None):
        self.settings = settings
        self.instance_id = uuid4()
        self.queue: asyncio.Queue[UUID] = asyncio.Queue(maxsize=settings.queue_capacity)
        self.accepting = False
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._orders: dict[UUID, Order] = {}
        self._requests: dict[UUID, tuple[str, UUID]] = {}
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._journal = Journal(settings.run_id, self.instance_id, settings.event_capacity, clock)

    def now(self):
        return self._clock()

    async def open(self):
        async with self._lock:
            self.accepting = True
            self._journal.append(StartedEvent, uuid4(), "order", 1, ServicePayload(status="UP"))

    async def close(self):
        async with self._lock:
            self.accepting = False

    async def mark_stopping(self):
        async with self._lock:
            self._journal.append(StoppingEvent, uuid4(), "order", 1, ServicePayload(status="STOPPING"))

    # ----------------------------------------------------------------- admission and reads

    async def submit(self, request, trace_id):
        if request.quantity > self.settings.max_quantity:
            raise OrderError(400, "INVALID_REQUEST", "Quantity exceeds the per-order limit",
                             {"maxQuantity": self.settings.max_quantity})
        if request.pizza_type not in self.settings.menu:
            raise OrderError(400, "UNKNOWN_PIZZA", "That pizza is not on the menu",
                             {"pizzaType": request.pizza_type, "menu": list(self.settings.menu)})
        fingerprint = json.dumps(request.model_dump(mode="json", exclude={"request_id"}), sort_keys=True)
        async with self._lock:
            existing = self._requests.get(request.request_id)
            if existing is not None:
                known_fingerprint, order_id = existing
                if known_fingerprint != fingerprint:
                    raise OrderError(409, "IDEMPOTENCY_CONFLICT", "Request ID already used for a different order",
                                     {"orderId": str(order_id)})
                return self._orders[order_id].model_copy(deep=True), False
            if not self.accepting:
                raise OrderError(503, "ORDER_UNAVAILABLE", "Order is not accepting new orders",
                                 {"retryAfterSeconds": 1}, {"Retry-After": "1"})
            if self.queue.full():
                raise OrderError(503, "QUEUE_FULL", "Order backlog is full",
                                 {"capacity": self.settings.queue_capacity, "retryAfterSeconds": 1}, {"Retry-After": "1"})
            now = self._clock()
            self._sequence += 1
            order = Order(order_id=uuid4(), request_id=request.request_id, customer=request.customer,
                          pizza_type=request.pizza_type, quantity=request.quantity, status=OrderStatus.RECEIVED,
                          sequence=self._sequence, version=1, trace_id=trace_id, created_at=now, updated_at=now)
            self._orders[order.order_id] = order
            self._requests[request.request_id] = (fingerprint, order.order_id)
            self.queue.put_nowait(order.order_id)
            self._journal.append(ReceivedEvent, trace_id, order.order_id, order.version, order.model_copy(deep=True), order.order_id)
            return order.model_copy(deep=True), True

    async def order(self, order_id):
        async with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise OrderError(404, "ORDER_NOT_FOUND", "No order exists with this ID")
            return order.model_copy(deep=True)

    async def get(self, order_id):
        async with self._lock:
            return self._orders[order_id].model_copy(deep=True)

    async def list(self, limit, cursor=None):
        if not 1 <= limit <= 100:
            raise OrderError(400, "INVALID_REQUEST", "limit must be between 1 and 100")
        before = decode_cursor(cursor) if cursor is not None else None
        async with self._lock:
            newest_first = sorted(self._orders.values(), key=lambda order: order.sequence, reverse=True)
            if before is not None:
                newest_first = [order for order in newest_first if order.sequence < before]
            page = [order.model_copy(deep=True) for order in newest_first[:limit]]
            next_cursor = encode_cursor(page[-1].sequence) if len(newest_first) > limit else None
            return OrderPage(orders=page, next_cursor=next_cursor)

    # ----------------------------------------------------------------- transitions

    async def advance(self, order_id, status=None, *, attempt=None, **changes):
        """Apply a change under the lock. A status change must be legal and emits exactly one event."""
        async with self._lock:
            order = self._orders[order_id]
            if attempt is not None:
                changes["attempts"] = [*order.attempts, attempt]
            event_class = None
            if status is not None and status != order.status:
                if status not in TRANSITIONS[order.status]:
                    raise IllegalTransition(order.status, status)
                changes["status"] = status
                event_class = STATUS_EVENTS[status]
            order = order.model_copy(update={**changes, "version": order.version + 1, "updated_at": self._clock()})
            self._orders[order_id] = order
            if event_class is not None:
                self._journal.append(event_class, order.trace_id, order_id, order.version, order.model_copy(deep=True), order_id)
            return order.model_copy(deep=True)

    async def trouble(self, order_id, error, attempt):
        event_class = TimeoutEvent if isinstance(error, DependencyLost) else UnavailableEvent
        payload = DependencyPayload(operation=error.operation, attempt=attempt, code=error.code, message=error.message,
                                    status=error.status)
        async with self._lock:
            order = self._orders[order_id]
            order = order.model_copy(update={
                "attempts": [*order.attempts, Attempt(operation=error.operation, outcome=error.code, at=self._clock())],
                "last_error": error.detail(), "version": order.version + 1, "updated_at": self._clock()})
            self._orders[order_id] = order
            self._journal.append(event_class, order.trace_id, order_id, order.version, payload, order_id)

    # ----------------------------------------------------------------- observability

    async def snapshot(self, workers, kitchen_instance_id):
        async with self._lock:
            return Snapshot(run_id=self.settings.run_id, instance_id=self.instance_id,
                            orders=[order.model_copy(deep=True) for order in self._orders.values()],
                            queue=QueueState(capacity=self.settings.queue_capacity, depth=self.queue.qsize(), workers=workers,
                                             accepting=self.accepting),
                            kitchen=DependencyState(url=self.settings.kitchen_url, instance_id=kitchen_instance_id),
                            menu=list(self.settings.menu), watermark=self._journal.cursor())

    async def events(self, instance_id, after, limit):
        async with self._lock:
            return self._journal.page(instance_id, after, limit)
