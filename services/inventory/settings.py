import os
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

DEFAULT_STOCK = {"DOUGH": 40, "SAUCE": 40, "CHEESE": 30, "PEPPERONI": 15, "BASIL": 15}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID = Field(default_factory=uuid4)
    max_quantity: StrictInt = Field(default=10_000, ge=1)
    stock_limit: StrictInt = Field(default=1_000_000, ge=1)
    event_capacity: StrictInt = Field(default=5_000, ge=1)
    max_delay_ms: StrictInt = Field(default=10_000, ge=0, le=10_000)
    initial_stock: dict[str, StrictInt] = Field(default_factory=lambda: DEFAULT_STOCK.copy())

    @model_validator(mode="after")
    def validate_stock(self):
        if self.initial_stock.keys() != DEFAULT_STOCK.keys():
            raise ValueError("Initial stock must contain exactly the five supported ingredients")
        if any(quantity < 0 or quantity > self.stock_limit for quantity in self.initial_stock.values()):
            raise ValueError("Initial quantities must be between zero and the stock limit")
        return self

    @classmethod
    def from_env(cls):
        values = {}
        if "PIZZAOPS_RUN_ID" in os.environ:
            values["run_id"] = os.environ["PIZZAOPS_RUN_ID"]
        for field in ("max_quantity", "stock_limit", "event_capacity", "max_delay_ms"):
            key = f"INVENTORY_{field.upper()}"
            if key in os.environ:
                values[field] = int(os.environ[key])
        return cls(**values)
