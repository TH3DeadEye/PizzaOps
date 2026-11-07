import logging
from collections import deque
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from services.kitchen.errors import KitchenError
from services.kitchen.models import Cursor, Job, Model, MoodState

LOGGER = logging.getLogger("pizzaops.kitchen")


def configure_logging():
    if not LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


class ServicePayload(Model):
    status: Literal["UP", "STOPPING"]


class StockCheckPayload(Model):
    authoritative: bool
    source: Literal["AUTHORITATIVE", "CACHED"]
    sufficient: bool
    needed: dict[str, int]
    available: dict[str, int]
    missing: dict[str, int]
    stock_version: int
    snapshot_version: int


class DependencyPayload(Model):
    dependency: Literal["inventory"] = "inventory"
    operation: Literal["stock", "reserve", "lookup"]
    attempt: int
    code: str
    message: str
    status: int | None = None


class MoodPayload(Model):
    reason: Literal["RESERVATION_SUCCEEDED", "RESERVATION_REJECTED", "DEPENDENCY_TROUBLE", "RECONCILE"]
    before: MoodState
    after: MoodState
    command_id: UUID | None = None


class RelationshipPayload(Model):
    reason: Literal["STALE_STOCK_CONTRADICTION"] = "STALE_STOCK_CONTRADICTION"
    before: MoodState
    after: MoodState
    needed: dict[str, int]
    claimed: dict[str, int]
    missing: dict[str, int]
    snapshot_version: int
    stock_version: int


class EventBase(Model):
    schema_version: Literal[1] = 1
    run_id: UUID
    instance_id: UUID
    service: Literal["kitchen"] = "kitchen"
    sequence: int
    timestamp: datetime
    trace_id: UUID
    order_id: UUID | None = None
    entity_id: str
    entity_version: int


class StartedEvent(EventBase):
    type: Literal["SERVICE_STARTED"] = "SERVICE_STARTED"
    payload: ServicePayload


class StoppingEvent(EventBase):
    type: Literal["SERVICE_STOPPING"] = "SERVICE_STOPPING"
    payload: ServicePayload


class QueuedEvent(EventBase):
    type: Literal["COOK_QUEUED"] = "COOK_QUEUED"
    payload: Job


class CheckingEvent(EventBase):
    type: Literal["COOK_CHECKING"] = "COOK_CHECKING"
    payload: Job


class StockCheckedEvent(EventBase):
    type: Literal["STOCK_CHECKED"] = "STOCK_CHECKED"
    payload: StockCheckPayload


class CookStartedEvent(EventBase):
    type: Literal["COOK_STARTED"] = "COOK_STARTED"
    payload: Job


class CompletedEvent(EventBase):
    type: Literal["COOK_COMPLETED"] = "COOK_COMPLETED"
    payload: Job


class FailedEvent(EventBase):
    type: Literal["COOK_FAILED"] = "COOK_FAILED"
    payload: Job


class ReconcilingEvent(EventBase):
    type: Literal["COOK_RECONCILING"] = "COOK_RECONCILING"
    payload: Job


class UnknownEvent(EventBase):
    type: Literal["COOK_UNKNOWN"] = "COOK_UNKNOWN"
    payload: Job


class RelationshipEvent(EventBase):
    type: Literal["RELATIONSHIP_CHANGED"] = "RELATIONSHIP_CHANGED"
    payload: RelationshipPayload


class MoodEvent(EventBase):
    type: Literal["MOOD_CHANGED"] = "MOOD_CHANGED"
    payload: MoodPayload


class TimeoutEvent(EventBase):
    type: Literal["DEPENDENCY_TIMEOUT"] = "DEPENDENCY_TIMEOUT"
    payload: DependencyPayload


class UnavailableEvent(EventBase):
    type: Literal["DEPENDENCY_UNAVAILABLE"] = "DEPENDENCY_UNAVAILABLE"
    payload: DependencyPayload


Event = Annotated[StartedEvent | StoppingEvent | QueuedEvent | CheckingEvent | StockCheckedEvent | CookStartedEvent
                  | CompletedEvent | FailedEvent | ReconcilingEvent | UnknownEvent | RelationshipEvent | MoodEvent
                  | TimeoutEvent | UnavailableEvent, Field(discriminator="type")]


class EventPage(Model):
    events: list[Event]
    next_cursor: Cursor
    has_more: bool


class Journal:
    def __init__(self, run_id, instance_id, capacity, clock=None):
        self.run_id = run_id
        self.instance_id = instance_id
        self.sequence = 0
        self._events = deque(maxlen=capacity)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def append(self, event_class, trace_id, entity_id, entity_version, payload, order_id=None):
        event = event_class(run_id=self.run_id, instance_id=self.instance_id, sequence=self.sequence + 1,
                            timestamp=self._clock(), trace_id=trace_id, order_id=order_id,
                            entity_id=str(entity_id), entity_version=entity_version, payload=payload)
        self.sequence += 1
        self._events.append(event)
        LOGGER.info(event.model_dump_json(by_alias=True))

    def cursor(self):
        return Cursor(instance_id=self.instance_id, sequence=self.sequence)

    def page(self, instance_id, after, limit):
        if after < 0 or not 1 <= limit <= 500:
            raise KitchenError(400, "INVALID_CURSOR", "Invalid event sequence or page limit")
        earliest = self._events[0].sequence if self._events else self.sequence + 1
        if instance_id != self.instance_id or after < earliest - 1:
            raise KitchenError(410, "EVENT_CURSOR_RESET", "Take a new snapshot before resuming events", {
                "instanceId": str(self.instance_id), "earliestSequence": earliest, "latestSequence": self.sequence})
        if after > self.sequence:
            raise KitchenError(400, "INVALID_CURSOR", "Invalid event sequence or page limit")
        events = [event.model_copy(deep=True) for event in self._events if event.sequence > after][:limit]
        sequence = events[-1].sequence if events else after
        return EventPage(events=events, next_cursor=Cursor(instance_id=self.instance_id, sequence=sequence),
                         has_more=sequence < self.sequence)
