"""Test harness: Inventory, Kitchen and Order in one process, talking only through ASGI transports.

Order reaches Kitchen through a ``ScriptedTransport`` so submissions can be lost before or after delivery; Kitchen
can be restarted (a fresh instance with a fresh in-memory state) to test state-loss reporting.
"""

import asyncio
from uuid import uuid4

import httpx
import pytest

from services.inventory.main import create_app as create_inventory_app
from services.inventory.settings import DEFAULT_STOCK, Settings as InventorySettings
from services.kitchen.inventory_client import InventoryClient
from services.kitchen.jobs import Kitchen
from services.kitchen.main import create_app as create_kitchen_app
from services.kitchen.settings import Settings as KitchenSettings
from services.order.kitchen_client import KitchenClient
from services.order.models import Customer, OrderRequest, OrderStatus, TERMINAL_STATUSES
from services.order.orders import OrderBook
from services.order.settings import Settings as OrderSettings
from services.order.workers import Fulfillment
from tests.kitchen.harness import FakeClock, Gate, ScriptedTransport  # noqa: F401  (re-exported for order tests)

SETTLED = TERMINAL_STATUSES | {OrderStatus.UNKNOWN}


def customer(trait="patient", appearance="customer_amber"):
    return Customer(id=uuid4(), appearance=appearance, trait=trait)


class Restaurant:
    def __init__(self, inventory_settings, kitchen_settings, order_settings, *, clock=None, sleep=asyncio.sleep,
                 kitchen_sleep=asyncio.sleep):
        self.inventory_settings = inventory_settings
        self.kitchen_settings = kitchen_settings
        self.order_settings = order_settings
        self.inventory_app = create_inventory_app(inventory_settings)
        inventory_transport = lambda: httpx.ASGITransport(app=self.inventory_app)  # noqa: E731
        self.kitchen_app = create_kitchen_app(kitchen_settings, kitchen_factory=lambda settings: Kitchen(
            settings, InventoryClient(settings, transport=inventory_transport()), sleep=kitchen_sleep))
        self.transport = ScriptedTransport(httpx.ASGITransport(app=self.kitchen_app))
        self.book = OrderBook(order_settings, clock=clock)
        self.service = Fulfillment(order_settings, self.book, KitchenClient(order_settings, transport=self.transport), sleep=sleep)
        self.admin = httpx.AsyncClient(transport=inventory_transport(), base_url="http://inventory",
                                       headers={"X-Run-Id": str(inventory_settings.run_id)})
        self._inventory_lifespan = None
        self._kitchen_lifespan = None

    @property
    def inventory(self):
        return self.inventory_app.state.inventory

    @property
    def kitchen(self):
        return self.kitchen_app.state.kitchen

    async def start_kitchen(self):
        self._kitchen_lifespan = self.kitchen_app.router.lifespan_context(self.kitchen_app)
        await self._kitchen_lifespan.__aenter__()

    async def stop_kitchen(self):
        await self._kitchen_lifespan.__aexit__(None, None, None)

    async def restart_kitchen(self):
        await self.stop_kitchen()
        await self.start_kitchen()

    async def __aenter__(self):
        self._inventory_lifespan = self.inventory_app.router.lifespan_context(self.inventory_app)
        await self._inventory_lifespan.__aenter__()
        await self.start_kitchen()
        await self.service.start()
        return self

    async def __aexit__(self, *exc):
        await self.service.stop()
        await self.stop_kitchen()
        await self.admin.aclose()
        await self._inventory_lifespan.__aexit__(None, None, None)

    async def stock(self):
        response = await self.admin.get("/stock", params={"authoritative": "true"})
        assert response.status_code == 200, response.text
        return response.json()["items"]

    async def place(self, pizza="CHEESE", quantity=1, request_id=None, who=None, trace_id=None):
        request = OrderRequest(request_id=request_id or uuid4(), customer=who or customer(), pizza_type=pizza, quantity=quantity)
        return await self.book.submit(request, trace_id or uuid4())

    async def wait_for(self, order_id, *statuses, timeout=5.0):
        statuses = set(statuses) or SETTLED
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            order = await self.book.order(order_id)
            if order.status in statuses:
                return order
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(f"Order {order_id} stayed {order.status} (wanted {sorted(statuses)}): {order.model_dump()}")
            await asyncio.sleep(0.005)

    async def wait_until(self, predicate, timeout=5.0, message="condition"):
        deadline = asyncio.get_running_loop().time() + timeout
        while not await predicate():
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(f"Timed out waiting for {message}")
            await asyncio.sleep(0.005)

    async def events(self):
        return (await self.book.events(self.book.instance_id, 0, 500)).events

    async def event_types(self, order_id=None):
        return [event.type for event in await self.events() if order_id is None or event.order_id == order_id]

    def calls(self, method=None, path_prefix=""):
        return [request for request in self.transport.requests
                if (method is None or request.method == method) and request.url.path.startswith(path_prefix)]


def restaurant(*, stock=None, clock=None, sleep=asyncio.sleep, kitchen_sleep=asyncio.sleep, kitchen=None, **order_overrides):
    inventory_settings = InventorySettings(initial_stock=DEFAULT_STOCK | (stock or {}))
    kitchen_defaults = {"cooking_s": 0, "attempt_timeout_s": 1, "retry_backoff_s": 0, "reconcile_backoff_s": 0.01,
                        "reconcile_window_s": 5, "drain_timeout_s": 0.1}
    kitchen_settings = KitchenSettings(run_id=inventory_settings.run_id, **{**kitchen_defaults, **(kitchen or {})})
    order_defaults = {"attempt_timeout_s": 1, "retry_backoff_s": 0, "poll_interval_s": 0.005, "observe_timeout_s": 5,
                      "reconcile_backoff_s": 0.01, "reconcile_window_s": 5, "drain_timeout_s": 1}
    order_settings = OrderSettings(run_id=inventory_settings.run_id, **{**order_defaults, **order_overrides})
    return Restaurant(inventory_settings, kitchen_settings, order_settings, clock=clock, sleep=sleep, kitchen_sleep=kitchen_sleep)
