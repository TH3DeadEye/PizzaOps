from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True)


IngredientName = Annotated[str, Field(pattern=r"^[A-Z][A-Z_]{0,31}$", strict=True)]
PositiveQuantity = Annotated[int, Field(strict=True, ge=1)]
Items = Annotated[dict[IngredientName, PositiveQuantity], Field(min_length=1, max_length=32)]


class ReservationRequest(Model):
    order_id: UUID
    items: Items


class RestockRequest(Model):
    command_id: UUID
    items: Items


class FailureMode(StrEnum):
    NONE = "NONE"
    UNAVAILABLE = "UNAVAILABLE"


class DelayPhase(StrEnum):
    BEFORE_MUTATION = "BEFORE_MUTATION"
    AFTER_MUTATION_BEFORE_RESPONSE = "AFTER_MUTATION_BEFORE_RESPONSE"


class FaultConfig(Model):
    stale_stock_enabled: StrictBool = False
    delay_ms: Annotated[int, Field(strict=True, ge=0)] = 0
    failure_mode: FailureMode = FailureMode.NONE
    delay_phase: DelayPhase = DelayPhase.BEFORE_MUTATION


class FaultRequest(Model):
    command_id: UUID
    stale_stock_enabled: StrictBool
    delay_ms: Annotated[int, Field(strict=True, ge=0)]
    failure_mode: FailureMode
    delay_phase: DelayPhase

    def config(self):
        return FaultConfig(**self.model_dump(exclude={"command_id"}))


class Reservation(Model):
    reservation_id: UUID
    order_id: UUID
    status: Literal["RESERVED"] = "RESERVED"
    items: dict[str, int]
    stock_version: int
    trace_id: UUID


class StockResponse(Model):
    items: dict[str, int]
    source: Literal["AUTHORITATIVE", "CACHED"]
    stock_version: int
    snapshot_version: int


class RestockResponse(Model):
    command_id: UUID
    items: dict[str, int]
    stock_version: int
    trace_id: UUID


class FaultResponse(Model):
    command_id: UUID
    faults: FaultConfig
    version: int
    snapshot_version: int | None
    trace_id: UUID


class Cursor(Model):
    instance_id: UUID
    sequence: int


class HealthResponse(Model):
    status: Literal["UP"] = "UP"
    service: Literal["inventory"] = "inventory"
    run_id: UUID
    instance_id: UUID


class Snapshot(Model):
    service: Literal["inventory"] = "inventory"
    run_id: UUID
    instance_id: UUID
    items: dict[str, int]
    stock_version: int
    reservations: list[Reservation]
    faults: FaultConfig
    fault_version: int
    watermark: Cursor
