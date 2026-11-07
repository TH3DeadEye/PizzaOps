from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True)


PizzaName = Annotated[str, Field(pattern=r"^[A-Z][A-Z_]{0,31}$", strict=True)]
PositiveQuantity = Annotated[int, Field(strict=True, ge=1)]
Trait = Literal["patient", "impatient", "chatty"]
Appearance = Literal["customer_amber", "customer_lavender", "customer_teal"]


class Customer(Model):
    id: UUID
    appearance: Appearance
    trait: Trait


class OrderRequest(Model):
    request_id: UUID
    customer: Customer
    pizza_type: PizzaName
    quantity: PositiveQuantity


class OrderStatus(StrEnum):
    RECEIVED = "RECEIVED"
    CHECKING = "CHECKING"
    COOKING = "COOKING"
    RECONCILING = "RECONCILING"
    READY = "READY"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATUSES = frozenset({OrderStatus.READY, OrderStatus.FAILED})

TRANSITIONS = {
    OrderStatus.RECEIVED: frozenset({OrderStatus.CHECKING, OrderStatus.FAILED}),
    OrderStatus.CHECKING: frozenset({OrderStatus.COOKING, OrderStatus.READY, OrderStatus.FAILED, OrderStatus.RECONCILING,
                                     OrderStatus.UNKNOWN}),
    OrderStatus.COOKING: frozenset({OrderStatus.READY, OrderStatus.FAILED, OrderStatus.RECONCILING, OrderStatus.UNKNOWN}),
    OrderStatus.RECONCILING: frozenset({OrderStatus.CHECKING, OrderStatus.COOKING, OrderStatus.READY, OrderStatus.FAILED,
                                        OrderStatus.UNKNOWN}),
    OrderStatus.READY: frozenset(),
    OrderStatus.FAILED: frozenset(),
    OrderStatus.UNKNOWN: frozenset({OrderStatus.RECONCILING}),
}


class ErrorDetail(Model):
    code: str
    message: str
    status: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Attempt(Model):
    operation: Literal["submit", "lookup"]
    outcome: str
    at: datetime


class Order(Model):
    order_id: UUID
    request_id: UUID
    customer: Customer
    pizza_type: str
    quantity: int
    status: OrderStatus
    failure_reason: str | None = None
    last_error: ErrorDetail | None = None
    job_id: UUID | None = None
    kitchen_status: str | None = None
    kitchen_instance_id: UUID | None = None
    reservation_id: UUID | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    sequence: int
    version: int
    trace_id: UUID
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class OrderPage(Model):
    orders: list[Order]
    next_cursor: str | None


class Cursor(Model):
    instance_id: UUID
    sequence: int


class HealthResponse(Model):
    status: Literal["UP"] = "UP"
    service: Literal["order"] = "order"
    run_id: UUID
    instance_id: UUID


class QueueState(Model):
    capacity: int
    depth: int
    workers: int
    accepting: bool


class DependencyState(Model):
    url: str
    instance_id: UUID | None


class Snapshot(Model):
    service: Literal["order"] = "order"
    run_id: UUID
    instance_id: UUID
    orders: list[Order]
    queue: QueueState
    kitchen: DependencyState
    menu: list[str]
    watermark: Cursor
