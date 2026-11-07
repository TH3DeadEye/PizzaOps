import os
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

DEFAULT_MENU = ("CHEESE", "PEPPERONI", "MARINARA")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID = Field(default_factory=uuid4)
    kitchen_url: str = "http://127.0.0.1:8081"
    menu: tuple[str, ...] = Field(default=DEFAULT_MENU, min_length=1)
    workers: StrictInt = Field(default=4, ge=1, le=32)
    queue_capacity: StrictInt = Field(default=50, ge=1)
    max_quantity: StrictInt = Field(default=20, ge=1)
    event_capacity: StrictInt = Field(default=5_000, ge=1)
    connect_timeout_s: float = Field(default=0.3, gt=0)
    attempt_timeout_s: float = Field(default=2.0, gt=0)
    retry_backoff_s: float = Field(default=0.1, ge=0)
    poll_interval_s: float = Field(default=0.1, ge=0)
    observe_timeout_s: float = Field(default=60.0, gt=0)
    reconcile_window_s: float = Field(default=30.0, gt=0)
    reconcile_attempts: StrictInt = Field(default=5, ge=1)
    reconcile_backoff_s: float = Field(default=0.25, ge=0)
    drain_timeout_s: float = Field(default=2.0, ge=0)

    @classmethod
    def from_env(cls):
        values = {}
        if "PIZZAOPS_RUN_ID" in os.environ:
            values["run_id"] = os.environ["PIZZAOPS_RUN_ID"]
        if "ORDER_KITCHEN_URL" in os.environ:
            values["kitchen_url"] = os.environ["ORDER_KITCHEN_URL"]
        if "ORDER_MENU" in os.environ:
            values["menu"] = tuple(item.strip() for item in os.environ["ORDER_MENU"].split(",") if item.strip())
        for name, field in cls.model_fields.items():
            key = f"ORDER_{name.upper()}"
            if key in os.environ and name not in ("run_id", "kitchen_url", "menu"):
                parse = int if field.annotation is int else float
                values[name] = parse(os.environ[key])
        return cls(**values)
