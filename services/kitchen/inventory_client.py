"""HTTP client for Inventory with explicit budgets and honest error translation.

Every outcome of a call falls into one of three classes, and the distinction drives the job state machine:

- ``DependencyRejected``: Inventory answered with a 4xx domain error. The outcome is known and nothing was allocated.
- ``DependencyUnavailable``: the request provably never took effect (connection refused, connect timeout, or an
  explicit 503 which Inventory only raises before mutating). Safe to retry once.
- ``DependencyLost``: the request may have taken effect but the answer is unknown (read timeout, broken connection,
  unexpected 5xx, unreadable body). Never retried blindly; the job moves to reconciliation.
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID

import httpx

from services.kitchen.models import ErrorDetail


class DependencyError(Exception):
    def __init__(self, operation, code, message, status=None, details=None):
        super().__init__(message)
        self.operation = operation
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def detail(self):
        return ErrorDetail(code=self.code, message=self.message, status=self.status,
                           details={"dependency": "inventory", "operation": self.operation, **self.details})


class DependencyRejected(DependencyError):
    """Known 4xx rejection; no allocation happened."""


class DependencyUnavailable(DependencyError):
    """Known non-delivery or explicit pre-mutation refusal; no allocation happened."""


class DependencyLost(DependencyError):
    """Ambiguous: the request may have been applied, the response was not observed."""


@dataclass(frozen=True)
class StockView:
    items: dict[str, int]
    source: str
    stock_version: int
    snapshot_version: int
    instance_id: UUID | None


@dataclass(frozen=True)
class ReservationView:
    reservation_id: UUID
    order_id: UUID
    items: dict[str, int]
    stock_version: int
    instance_id: UUID | None


class InventoryClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.instance_id: UUID | None = None
        timeout = httpx.Timeout(settings.attempt_timeout_s, connect=settings.connect_timeout_s)
        self._client = httpx.AsyncClient(base_url=settings.inventory_url, timeout=timeout, transport=transport, trust_env=False)

    async def aclose(self):
        await self._client.aclose()

    async def stock(self, trace_id, *, authoritative=False):
        body, instance = await self._request("GET", "/stock", "stock", trace_id,
                                             params={"authoritative": "true" if authoritative else "false"})
        try:
            return StockView(dict(body["items"]), body["source"], int(body["stockVersion"]), int(body["snapshotVersion"]), instance)
        except (KeyError, TypeError, ValueError) as error:
            raise DependencyLost("stock", "INVALID_RESPONSE", "Inventory returned an unexpected stock payload") from error

    async def reserve(self, order_id, items, trace_id):
        body, instance = await self._request("POST", "/reservations", "reserve", trace_id,
                                             json={"orderId": str(order_id), "items": dict(items)})
        return self._reservation("reserve", body, instance)

    async def reservation(self, order_id, trace_id):
        try:
            body, instance = await self._request("GET", f"/reservations/{order_id}", "lookup", trace_id)
        except DependencyRejected as error:
            if error.code == "RESERVATION_NOT_FOUND":
                return None
            raise
        return self._reservation("lookup", body, instance)

    def _reservation(self, operation, body, instance):
        try:
            return ReservationView(UUID(body["reservationId"]), UUID(body["orderId"]), dict(body["items"]),
                                   int(body["stockVersion"]), instance)
        except (KeyError, TypeError, ValueError) as error:
            raise DependencyLost(operation, "INVALID_RESPONSE", "Inventory returned an unexpected reservation payload") from error

    async def _request(self, method, path, operation, trace_id, **kwargs):
        headers = {"X-Run-Id": str(self.settings.run_id), "X-Trace-Id": str(trace_id)}
        budget = self.settings.attempt_timeout_s
        try:
            async with asyncio.timeout(budget):
                response = await self._client.request(method, path, headers=headers, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            raise DependencyUnavailable(operation, "CONNECTION_FAILED", f"Could not connect to inventory: {error}") from error
        except (TimeoutError, httpx.TimeoutException) as error:
            raise DependencyLost(operation, "TIMEOUT", f"Inventory did not answer within {budget:g}s") from error
        except httpx.HTTPError as error:
            raise DependencyLost(operation, "CONNECTION_LOST", f"Connection to inventory failed: {error}") from error
        instance = self._observe(response)
        if response.status_code >= 400:
            code, message, details = self._error_body(response)
            if response.status_code == 503:
                raise DependencyUnavailable(operation, code, message, response.status_code, details)
            if response.status_code >= 500:
                raise DependencyLost(operation, code, message, response.status_code, details)
            raise DependencyRejected(operation, code, message, response.status_code, details)
        try:
            return response.json(), instance
        except ValueError as error:
            raise DependencyLost(operation, "INVALID_RESPONSE", "Inventory returned a non-JSON body") from error

    def _observe(self, response):
        try:
            instance = UUID(response.headers["X-Instance-Id"])
        except (KeyError, ValueError):
            return None
        self.instance_id = instance
        return instance

    @staticmethod
    def _error_body(response):
        try:
            body = response.json()
            return str(body["code"]), str(body["message"]), dict(body.get("details") or {})
        except (ValueError, KeyError, TypeError):
            return f"HTTP_{response.status_code}", response.text[:200] or response.reason_phrase, {}
