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


class CookRequest(Model):
    order_id: UUID
    pizza_type: PizzaName
    quantity: PositiveQuantity


class ReconcileRequest(Model):
    command_id: UUID


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    CHECKING = "CHECKING"
    COOKING = "COOKING"
    COOKED = "COOKED"
    FAILED = "FAILED"
    RECONCILING = "RECONCILING"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATUSES = frozenset({JobStatus.COOKED, JobStatus.FAILED})


class ErrorDetail(Model):
    code: str
    message: str
    status: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Attempt(Model):
    operation: Literal["stock", "reserve", "lookup"]
    outcome: str
    at: datetime


class Job(Model):
    job_id: UUID
    order_id: UUID
    pizza_type: str
    quantity: int
    items: dict[str, int]
    status: JobStatus
    result: Literal["COOKED"] | None = None
    failure_reason: str | None = None
    last_error: ErrorDetail | None = None
    reservation_id: UUID | None = None
    inventory_instance_id: UUID | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    version: int
    trace_id: UUID
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


MoodLabel = Literal["CALM", "TENSE", "SUSPICIOUS", "FURIOUS"]


class MoodState(Model):
    patience: int
    stress: int
    trust: int
    failed_reservation_count: int
    mood: MoodLabel
    version: int


class ReconcileResponse(Model):
    command_id: UUID
    before: MoodState
    mood: MoodState
    trace_id: UUID


class Cursor(Model):
    instance_id: UUID
    sequence: int


class HealthResponse(Model):
    status: Literal["UP"] = "UP"
    service: Literal["kitchen"] = "kitchen"
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
    service: Literal["kitchen"] = "kitchen"
    run_id: UUID
    instance_id: UUID
    jobs: list[Job]
    mood: MoodState
    queue: QueueState
    inventory: DependencyState
    recipes: dict[str, dict[str, int]]
    watermark: Cursor
