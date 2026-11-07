import asyncio
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from services.inventory.errors import InventoryError
from services.inventory.events import (
    FaultEvent, FaultPayload, Journal, RejectedEvent, RejectedPayload, ReservedEvent,
    RestockedEvent, RestockedPayload, ServicePayload, StartedEvent, StockReadEvent, StoppingEvent,
)
from services.inventory.faults import after_operation, before_operation
from services.inventory.models import FaultConfig, FaultResponse, Reservation, RestockResponse, Snapshot, StockResponse


@dataclass(frozen=True)
class CommandResult:
    fingerprint: str
    response: RestockResponse | FaultResponse


class InventoryStore:
    def __init__(self, settings, *, clock=None, sleep=asyncio.sleep):
        self.settings = settings
        self.instance_id = uuid4()
        self._stock = settings.initial_stock.copy()
        self._version = 1
        self._reservations: dict[UUID, Reservation] = {}
        self._commands: dict[UUID, CommandResult] = {}
        self._faults = FaultConfig()
        self._fault_version = 0
        self._cached_stock = None
        self._cached_version = None
        self._lock = asyncio.Lock()
        self._sleep = sleep
        self._journal = Journal(settings.run_id, self.instance_id, settings.event_capacity, clock)

    async def lifecycle(self, starting):
        async with self._lock:
            self._journal.append(StartedEvent if starting else StoppingEvent, uuid4(), "inventory", 1,
                                 ServicePayload(status="UP" if starting else "STOPPING"))

    def _check_items(self, items):
        if any(quantity > self.settings.max_quantity for quantity in items.values()):
            raise InventoryError(400, "INVALID_REQUEST", "Ingredient quantity exceeds the per-request limit",
                                 {"maxQuantity": self.settings.max_quantity})
        unknown = sorted(set(items) - self._stock.keys())
        if unknown:
            raise InventoryError(404, "UNKNOWN_INGREDIENT", "Unknown ingredient", {"ingredients": unknown})

    def _existing_reservation(self, request):
        existing = self._reservations.get(request.order_id)
        if existing is not None:
            if existing.items != request.items:
                raise InventoryError(409, "IDEMPOTENCY_CONFLICT", "Order already reserved different ingredients")
            return existing.model_copy(deep=True)
        return None

    def _existing_command(self, command_id, fingerprint):
        existing = self._commands.get(command_id)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise InventoryError(409, "IDEMPOTENCY_CONFLICT", "Command ID already used for different input")
            return existing.response.model_copy(deep=True)
        return None

    async def reserve(self, request, trace_id):
        async with self._lock:
            existing = self._existing_reservation(request)
            if existing is not None:
                return existing
            config = self._faults
        await before_operation(config, self._sleep)
        async with self._lock:
            existing = self._existing_reservation(request)
            if existing is not None:
                return existing
            try:
                self._check_items(request.items)
                missing = {name: quantity - self._stock[name] for name, quantity in request.items.items()
                           if self._stock[name] < quantity}
                if missing:
                    raise InventoryError(409, "INSUFFICIENT_STOCK", "Not enough ingredients", {
                        "missing": missing, "available": self._stock.copy(), "stockVersion": self._version,
                        "source": "AUTHORITATIVE"})
            except InventoryError as error:
                self._journal.append(RejectedEvent, trace_id, request.order_id, self._version,
                                     RejectedPayload(code=error.code, items=request.items.copy(), stock_version=self._version),
                                     request.order_id)
                raise
            reservation = Reservation(reservation_id=uuid4(), order_id=request.order_id, items=request.items.copy(),
                                      stock_version=self._version + 1, trace_id=trace_id)
            for name, quantity in request.items.items():
                self._stock[name] -= quantity
            self._version += 1
            self._reservations[request.order_id] = reservation
            self._journal.append(ReservedEvent, trace_id, request.order_id, 1, reservation.model_copy(deep=True), request.order_id)
            result = reservation.model_copy(deep=True)
        await after_operation(config, self._sleep)
        return result

    async def stock(self, trace_id, ingredient=None, authoritative=False):
        async with self._lock:
            config = self._faults
            cached_stock = self._cached_stock.copy() if self._cached_stock is not None else None
            cached_version = self._cached_version
        await before_operation(config, self._sleep)
        async with self._lock:
            if ingredient is not None and ingredient not in self._stock:
                raise InventoryError(404, "UNKNOWN_INGREDIENT", "Unknown ingredient", {"ingredients": [ingredient]})
            cached = config.stale_stock_enabled and not authoritative
            quantities = cached_stock if cached else self._stock
            items = {ingredient: quantities[ingredient]} if ingredient is not None else quantities.copy()
            response = StockResponse(items=items, source="CACHED" if cached else "AUTHORITATIVE",
                                     stock_version=self._version, snapshot_version=cached_version if cached else self._version)
            self._journal.append(StockReadEvent, trace_id, "stock", self._version, response.model_copy(deep=True))
        await after_operation(config, self._sleep)
        return response

    async def reservation(self, order_id):
        async with self._lock:
            config = self._faults
        await before_operation(config, self._sleep)
        async with self._lock:
            record = self._reservations.get(order_id)
            if record is None:
                raise InventoryError(404, "RESERVATION_NOT_FOUND", "No successful reservation exists for this order")
            result = record.model_copy(deep=True)
        await after_operation(config, self._sleep)
        return result

    async def restock(self, request, trace_id):
        fingerprint = json.dumps({"operation": "restock", "items": request.items}, sort_keys=True)
        async with self._lock:
            existing = self._existing_command(request.command_id, fingerprint)
            if existing is not None:
                return existing
            self._check_items(request.items)
            exceeded = [name for name, quantity in request.items.items() if self._stock[name] + quantity > self.settings.stock_limit]
            if exceeded:
                raise InventoryError(409, "STOCK_LIMIT_EXCEEDED", "Restock exceeds the stock limit", {"ingredients": sorted(exceeded)})
            for name, quantity in request.items.items():
                self._stock[name] += quantity
            self._version += 1
            response = RestockResponse(command_id=request.command_id, items=self._stock.copy(), stock_version=self._version, trace_id=trace_id)
            self._commands[request.command_id] = CommandResult(fingerprint, response)
            self._journal.append(RestockedEvent, trace_id, "stock", self._version,
                                 RestockedPayload(command_id=request.command_id, added=request.items.copy(),
                                                  items=self._stock.copy(), stock_version=self._version))
            return response.model_copy(deep=True)

    async def set_faults(self, request, trace_id):
        config = request.config()
        fingerprint = json.dumps({"operation": "faults", **config.model_dump(mode="json")}, sort_keys=True)
        async with self._lock:
            existing = self._existing_command(request.command_id, fingerprint)
            if existing is not None:
                return existing
            if config.delay_ms > self.settings.max_delay_ms:
                raise InventoryError(400, "INVALID_REQUEST", "Delay exceeds configured maximum", {"maxDelayMs": self.settings.max_delay_ms})
            if config.stale_stock_enabled and not self._faults.stale_stock_enabled:
                self._cached_stock = self._stock.copy()
                self._cached_version = self._version
            elif not config.stale_stock_enabled:
                self._cached_stock = None
                self._cached_version = None
            self._faults = config
            self._fault_version += 1
            response = FaultResponse(command_id=request.command_id, faults=config, version=self._fault_version,
                                     snapshot_version=self._cached_version, trace_id=trace_id)
            self._commands[request.command_id] = CommandResult(fingerprint, response)
            self._journal.append(FaultEvent, trace_id, "faults", self._fault_version,
                                 FaultPayload(command_id=request.command_id, faults=config, snapshot_version=self._cached_version))
            return response.model_copy(deep=True)

    async def snapshot(self):
        async with self._lock:
            return Snapshot(run_id=self.settings.run_id, instance_id=self.instance_id, items=self._stock.copy(),
                            stock_version=self._version, reservations=[record.model_copy(deep=True) for record in self._reservations.values()],
                            faults=self._faults.model_copy(deep=True), fault_version=self._fault_version,
                            watermark=self._journal.cursor())

    async def events(self, instance_id, after, limit):
        async with self._lock:
            return self._journal.page(instance_id, after, limit)
