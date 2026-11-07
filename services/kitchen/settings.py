import os
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID = Field(default_factory=uuid4)
    inventory_url: str = "http://127.0.0.1:8082"
    workers: StrictInt = Field(default=2, ge=1, le=16)
    queue_capacity: StrictInt = Field(default=20, ge=1)
    max_quantity: StrictInt = Field(default=20, ge=1)
    event_capacity: StrictInt = Field(default=5_000, ge=1)
    connect_timeout_s: float = Field(default=0.3, gt=0)
    attempt_timeout_s: float = Field(default=2.0, gt=0)
    retry_backoff_s: float = Field(default=0.1, ge=0)
    job_deadline_s: float = Field(default=15.0, gt=0)
    cooking_s: float = Field(default=0.25, ge=0)
    reconcile_window_s: float = Field(default=30.0, gt=0)
    reconcile_attempts: StrictInt = Field(default=5, ge=1)
    reconcile_backoff_s: float = Field(default=0.25, ge=0)
    drain_timeout_s: float = Field(default=2.0, ge=0)
    initial_patience: StrictInt = Field(default=85, ge=0, le=100)
    initial_trust: StrictInt = Field(default=60, ge=0, le=100)
    reconcile_patience: StrictInt = Field(default=15, ge=0, le=100)
    reconcile_trust: StrictInt = Field(default=10, ge=0, le=100)

    @classmethod
    def from_env(cls):
        values = {}
        if "PIZZAOPS_RUN_ID" in os.environ:
            values["run_id"] = os.environ["PIZZAOPS_RUN_ID"]
        if "KITCHEN_INVENTORY_URL" in os.environ:
            values["inventory_url"] = os.environ["KITCHEN_INVENTORY_URL"]
        for name, field in cls.model_fields.items():
            key = f"KITCHEN_{name.upper()}"
            if key in os.environ and name not in ("run_id", "inventory_url"):
                parse = int if field.annotation is int else float
                values[name] = parse(os.environ[key])
        return cls(**values)
