"""HTTP client for Kitchen with explicit budgets and the same three-way error translation as Kitchen uses for Inventory.

- ``DependencyRejected``: a 4xx domain answer; the outcome is known and no job was created.
- ``DependencyUnavailable``: provably never took effect (connection refused, connect timeout, 503 before acceptance).
- ``DependencyLost``: the submission may have been accepted but the answer is unknown; reconcile, never retry blindly.
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID

import httpx

from services.order.models import ErrorDetail


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
                           details={"dependency": "kitchen", "operation": self.operation, **self.details})


class DependencyRejected(DependencyError):
    """Known 4xx rejection; no job exists."""


class DependencyUnavailable(DependencyError):
    """Known non-delivery or explicit pre-acceptance refusal; no job exists."""


class DependencyLost(DependencyError):
    """Ambiguous: the request may have been applied, the response was not observed."""


@dataclass(frozen=True)
class JobView:
    job_id: UUID
    order_id: UUID
    status: str
    result: str | None
    failure_reason: str | None
    last_error: dict | None
    reservation_id: UUID | None
    version: int
    instance_id: UUID | None


class KitchenClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.instance_id: UUID | None = None
        timeout = httpx.Timeout(settings.attempt_timeout_s, connect=settings.connect_timeout_s)
        self._client = httpx.AsyncClient(base_url=settings.kitchen_url, timeout=timeout, transport=transport, trust_env=False)

    async def aclose(self):
        await self._client.aclose()

    async def submit(self, order_id, pizza_type, quantity, trace_id):
        body, instance = await self._request("POST", "/cook", "submit", trace_id,
                                             json={"orderId": str(order_id), "pizzaType": pizza_type, "quantity": quantity})
        return self._job("submit", body, instance)

    async def job(self, order_id, trace_id):
        try:
            body, instance = await self._request("GET", f"/cook/{order_id}", "lookup", trace_id)
        except DependencyRejected as error:
            if error.code == "COOK_JOB_NOT_FOUND":
                return None
            raise
        return self._job("lookup", body, instance)

    @staticmethod
    def _job(operation, body, instance):
        try:
            return JobView(UUID(body["jobId"]), UUID(body["orderId"]), str(body["status"]), body.get("result"),
                           body.get("failureReason"), body.get("lastError"),
                           UUID(body["reservationId"]) if body.get("reservationId") else None, int(body["version"]), instance)
        except (KeyError, TypeError, ValueError) as error:
            raise DependencyLost(operation, "INVALID_RESPONSE", "Kitchen returned an unexpected job payload") from error

    async def _request(self, method, path, operation, trace_id, **kwargs):
        headers = {"X-Run-Id": str(self.settings.run_id), "X-Trace-Id": str(trace_id)}
        budget = self.settings.attempt_timeout_s
        try:
            async with asyncio.timeout(budget):
                response = await self._client.request(method, path, headers=headers, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            raise DependencyUnavailable(operation, "CONNECTION_FAILED", f"Could not connect to kitchen: {error}") from error
        except (TimeoutError, httpx.TimeoutException) as error:
            raise DependencyLost(operation, "TIMEOUT", f"Kitchen did not answer within {budget:g}s") from error
        except httpx.HTTPError as error:
            raise DependencyLost(operation, "CONNECTION_LOST", f"Connection to kitchen failed: {error}") from error
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
            raise DependencyLost(operation, "INVALID_RESPONSE", "Kitchen returned a non-JSON body") from error

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
