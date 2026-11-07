"""Test harness: a real in-process Inventory app wired to a Kitchen through a scriptable ASGI transport.

The transport can refuse connections, lose a request before delivery, lose a response after delivery, or run a
callback in the middle of a call. That reproduces the ambiguous outcomes a network produces without wall-clock
races.
"""

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from services.inventory.main import create_app as create_inventory_app
from services.inventory.settings import DEFAULT_STOCK, Settings as InventorySettings
from services.kitchen.inventory_client import InventoryClient
from services.kitchen.jobs import Kitchen
from services.kitchen.models import CookRequest, JobStatus, TERMINAL_STATUSES
from services.kitchen.settings import Settings as KitchenSettings

SETTLED = TERMINAL_STATUSES | {JobStatus.UNKNOWN}


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Gate:
    """A sleep replacement that blocks until released, so a worker can be held mid-job deterministically."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def sleep(self, seconds):
        self.entered.set()
        await self.release.wait()


class ScriptedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner):
        self.inner = inner
        self.plans = defaultdict(deque)
        self.requests = []

    def plan(self, method, path_prefix, *modes):
        self.plans[(method, path_prefix)].extend(modes)

    def _next_mode(self, request):
        for (method, prefix), modes in self.plans.items():
            if modes and request.method == method and request.url.path.startswith(prefix):
                return modes.popleft()
        return None

    async def handle_async_request(self, request):
        self.requests.append(request)
        mode = self._next_mode(request)
        if callable(mode):
            mode = await mode()
        if mode == "refuse":
            raise httpx.ConnectError("connection refused", request=request)
        if mode == "lose_request":
            raise httpx.ReadTimeout("request lost before delivery", request=request)
        response = await self.inner.handle_async_request(request)
        if mode == "lose_response":
            await response.aread()
            raise httpx.ReadTimeout("response lost after delivery", request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()


class Restaurant:
    def __init__(self, inventory_settings, kitchen_settings, *, clock=None, sleep=asyncio.sleep):
        self.inventory_settings = inventory_settings
        self.kitchen_settings = kitchen_settings
        self.inventory_app = create_inventory_app(inventory_settings)
        self.transport = ScriptedTransport(httpx.ASGITransport(app=self.inventory_app))
        self.kitchen = Kitchen(kitchen_settings, InventoryClient(kitchen_settings, transport=self.transport),
                               clock=clock, sleep=sleep)
        self.admin = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.inventory_app), base_url="http://inventory",
                                       headers={"X-Run-Id": str(inventory_settings.run_id)})
        self._lifespan = None

    @property
    def inventory(self):
        return self.inventory_app.state.inventory

    async def start_inventory(self):
        self._lifespan = self.inventory_app.router.lifespan_context(self.inventory_app)
        await self._lifespan.__aenter__()

    async def stop_inventory(self):
        await self._lifespan.__aexit__(None, None, None)

    async def restart_inventory(self):
        await self.stop_inventory()
        await self.start_inventory()

    async def __aenter__(self):
        await self.start_inventory()
        await self.kitchen.start()
        return self

    async def __aexit__(self, *exc):
        await self.kitchen.stop()
        await self.admin.aclose()
        await self.stop_inventory()

    async def stock(self):
        response = await self.admin.get("/stock", params={"authoritative": "true"})
        assert response.status_code == 200, response.text
        return response.json()["items"]

    async def set_faults(self, **values):
        body = {"commandId": str(uuid4()), "staleStockEnabled": False, "delayMs": 0, "failureMode": "NONE",
                "delayPhase": "BEFORE_MUTATION", **values}
        response = await self.admin.post("/admin/faults", json=body)
        assert response.status_code == 200, response.text

    async def reserve_elsewhere(self, items):
        response = await self.admin.post("/reservations", json={"orderId": str(uuid4()), "items": items})
        assert response.status_code == 200, response.text
        return response.json()

    async def submit(self, pizza="CHEESE", quantity=1, order_id=None, trace_id=None):
        request = CookRequest(order_id=order_id or uuid4(), pizza_type=pizza, quantity=quantity)
        return await self.kitchen.submit(request, trace_id or uuid4())

    async def wait_for(self, order_id, *statuses, timeout=5.0):
        statuses = set(statuses) or SETTLED
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            job = await self.kitchen.job(order_id)
            if job.status in statuses:
                return job
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(f"Job {order_id} stayed {job.status} (wanted {sorted(statuses)}): {job.model_dump()}")
            await asyncio.sleep(0.005)

    async def events(self):
        page = await self.kitchen.events(self.kitchen.instance_id, 0, 500)
        return page.events

    async def event_types(self, order_id=None):
        return [event.type for event in await self.events() if order_id is None or event.order_id == order_id]

    def calls(self, method=None, path_prefix=""):
        return [request for request in self.transport.requests
                if (method is None or request.method == method) and request.url.path.startswith(path_prefix)]


def restaurant(*, stock=None, clock=None, sleep=asyncio.sleep, **kitchen_overrides):
    inventory_settings = InventorySettings(initial_stock=DEFAULT_STOCK | (stock or {}))
    defaults = {"cooking_s": 0, "attempt_timeout_s": 1, "retry_backoff_s": 0, "reconcile_backoff_s": 0.01,
                "reconcile_window_s": 5, "drain_timeout_s": 1}
    kitchen_settings = KitchenSettings(run_id=inventory_settings.run_id, **{**defaults, **kitchen_overrides})
    return Restaurant(inventory_settings, kitchen_settings, clock=clock, sleep=sleep)
