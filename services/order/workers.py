"""Fulfillment: bounded workers that submit orders to Kitchen, reconcile lost answers and observe cooking jobs.

READY is earned: it is written only after Kitchen reported COOKED. A lost submission response moves the order to
RECONCILING and is resolved by looking the job up under the same order ID; a changed Kitchen instance or an exhausted
budget ends in UNKNOWN with the reason, never in an invented verdict.
"""

import asyncio
from datetime import timedelta

from services.order.events import LOGGER
from services.order.kitchen_client import DependencyLost, DependencyRejected, DependencyUnavailable
from services.order.models import Attempt, ErrorDetail, OrderStatus, TERMINAL_STATUSES

REJECTION_REASONS = {
    "UNKNOWN_PIZZA": "UNKNOWN_PIZZA",
    "IDEMPOTENCY_CONFLICT": "COOK_CONFLICT",
    "RUN_MISMATCH": "DEPENDENCY_RUN_MISMATCH",
}
UNKNOWN_REASONS = frozenset({"DEPENDENCY_STATE_LOST", "FULFILLMENT_UNCONFIRMED"})


class Unconfirmed(Exception):
    def __init__(self, reason, error):
        super().__init__(reason)
        self.reason = reason
        self.error = error


class Fulfillment:
    def __init__(self, settings, book, kitchen, *, sleep=asyncio.sleep):
        self.settings = settings
        self.book = book
        self.kitchen = kitchen
        self.instance_id = book.instance_id
        self._sleep = sleep
        self._workers: list[asyncio.Task] = []

    @property
    def worker_count(self):
        return len(self._workers)

    async def start(self):
        await self.book.open()
        self._workers = [asyncio.create_task(self._worker(), name=f"order-worker-{index}")
                         for index in range(self.settings.workers)]

    async def stop(self):
        await self.book.close()
        try:
            await asyncio.wait_for(self.book.queue.join(), self.settings.drain_timeout_s)
        except TimeoutError:
            pass
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        await self.book.mark_stopping()
        await self.kitchen.aclose()

    async def snapshot(self):
        return await self.book.snapshot(len(self._workers), self.kitchen.instance_id)

    # ----------------------------------------------------------------- workers

    async def _worker(self):
        while True:
            order_id = await self.book.queue.get()
            try:
                await self._process(order_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.exception("Order worker failed; orderId=%s", order_id)
                try:
                    await self._abandon(order_id, error)
                except Exception:
                    LOGGER.exception("Order could not record the failure; orderId=%s", order_id)
            finally:
                self.book.queue.task_done()

    async def _abandon(self, order_id, error):
        order = await self.book.get(order_id)
        if order.status in TERMINAL_STATUSES or order.status == OrderStatus.UNKNOWN:
            return
        detail = ErrorDetail(code="INTERNAL_ERROR", message=str(error) or type(error).__name__)
        await self.book.advance(order_id, OrderStatus.UNKNOWN, failure_reason="FULFILLMENT_UNCONFIRMED", last_error=detail)

    async def _process(self, order_id):
        order = await self.book.get(order_id)
        if order.status != OrderStatus.RECEIVED:
            return
        order = await self.book.advance(order_id, OrderStatus.CHECKING)
        try:
            view = await self._submit(order)
        except DependencyRejected as rejected:
            await self._fail(order_id, REJECTION_REASONS.get(rejected.code, "KITCHEN_REJECTED"), rejected.detail())
        except DependencyUnavailable as unavailable:
            reason = "KITCHEN_QUEUE_FULL" if unavailable.code == "QUEUE_FULL" else "KITCHEN_UNAVAILABLE"
            await self._fail(order_id, reason, unavailable.detail())
        except Unconfirmed as unconfirmed:
            await self.book.advance(order_id, OrderStatus.UNKNOWN, failure_reason=unconfirmed.reason, last_error=unconfirmed.error)
        else:
            await self._observe(order, view)

    async def _submit(self, order):
        attempt = 0
        while True:
            attempt += 1
            try:
                view = await self.kitchen.submit(order.order_id, order.pizza_type, order.quantity, order.trace_id)
            except DependencyUnavailable as error:
                await self.book.trouble(order.order_id, error, attempt)
                if attempt > 1:
                    raise
                await self._sleep(self.settings.retry_backoff_s)
            except DependencyLost as error:
                await self.book.trouble(order.order_id, error, attempt)
                return await self._reconcile(order, attempt)
            except DependencyRejected as error:
                await self.book.advance(order.order_id, attempt=self._attempt("submit", error.code))
                raise
            else:
                await self.book.advance(order.order_id, attempt=self._attempt("submit", "ACCEPTED"), job_id=view.job_id,
                                        kitchen_instance_id=view.instance_id, kitchen_status=view.status)
                return view

    async def _reconcile(self, order, attempt):
        expected = order.kitchen_instance_id or self.kitchen.instance_id
        await self.book.advance(order.order_id, OrderStatus.RECONCILING, kitchen_instance_id=expected)
        started = self.book.now()
        window = timedelta(seconds=self.settings.reconcile_window_s)
        rounds = 0
        for rounds in range(1, self.settings.reconcile_attempts + 1):
            await self._sleep(self.settings.reconcile_backoff_s * 2 ** (rounds - 1))
            if self.book.now() - started >= window:
                break
            attempt += 1
            try:
                found = await self.kitchen.job(order.order_id, order.trace_id)
            except (DependencyLost, DependencyUnavailable) as trouble:
                await self.book.trouble(order.order_id, trouble, attempt)
                continue
            except DependencyRejected as rejected:
                # Kitchen knowingly refused to answer the lookup. That says nothing about the earlier, ambiguous
                # submission, so the uncertainty stays on the record instead of becoming an invented failure.
                await self.book.advance(order.order_id, attempt=self._attempt("lookup", rejected.code))
                self._check_instance(expected)
                raise Unconfirmed("FULFILLMENT_UNCONFIRMED", rejected.detail()) from rejected
            self._check_instance(expected)
            if found is not None:
                await self.book.advance(order.order_id, attempt=self._attempt("lookup", "FOUND"), job_id=found.job_id,
                                        kitchen_status=found.status)
                return found
            await self.book.advance(order.order_id, attempt=self._attempt("lookup", "NOT_FOUND"))
            attempt += 1
            try:
                view = await self.kitchen.submit(order.order_id, order.pizza_type, order.quantity, order.trace_id)
            except (DependencyLost, DependencyUnavailable) as trouble:
                await self.book.trouble(order.order_id, trouble, attempt)
                continue
            except DependencyRejected as rejected:
                # Nothing was found under this key on the same instance, so this refusal is the whole story.
                await self.book.advance(order.order_id, attempt=self._attempt("submit", rejected.code))
                raise
            await self.book.advance(order.order_id, attempt=self._attempt("submit", "ACCEPTED"), job_id=view.job_id,
                                    kitchen_instance_id=view.instance_id, kitchen_status=view.status)
            return view
        raise Unconfirmed("FULFILLMENT_UNCONFIRMED", ErrorDetail(
            code="FULFILLMENT_UNCONFIRMED",
            message="Could not confirm whether Kitchen accepted the order within the reconciliation window",
            details={"rounds": rounds, "windowSeconds": self.settings.reconcile_window_s}))

    def _check_instance(self, expected):
        observed = self.kitchen.instance_id
        if expected is not None and observed is not None and observed != expected:
            raise Unconfirmed("DEPENDENCY_STATE_LOST", ErrorDetail(
                code="DEPENDENCY_STATE_LOST",
                message="Kitchen restarted while the submission outcome was unknown; its earlier state is gone",
                details={"expectedInstanceId": str(expected), "observedInstanceId": str(observed)}))

    async def _observe(self, order, view):
        started = self.book.now()
        window = timedelta(seconds=self.settings.observe_timeout_s)
        polls = 0
        while True:
            if view is not None and await self._apply(order.order_id, view):
                return
            if self.book.now() - started >= window:
                await self.book.advance(order.order_id, OrderStatus.UNKNOWN, failure_reason="FULFILLMENT_UNCONFIRMED",
                                        last_error=ErrorDetail(
                                            code="FULFILLMENT_UNCONFIRMED",
                                            message="Kitchen did not report a final result within the observation window",
                                            details={"windowSeconds": self.settings.observe_timeout_s,
                                                     "lastKitchenStatus": view.status if view is not None else None}))
                return
            await self._sleep(self.settings.poll_interval_s)
            polls += 1
            try:
                view = await self.kitchen.job(order.order_id, order.trace_id)
            except (DependencyLost, DependencyUnavailable) as trouble:
                await self.book.trouble(order.order_id, trouble, polls)
                view = None
                continue
            if view is None:
                current = await self.book.get(order.order_id)
                await self.book.advance(order.order_id, OrderStatus.UNKNOWN, failure_reason="DEPENDENCY_STATE_LOST",
                                        last_error=ErrorDetail(
                                            code="DEPENDENCY_STATE_LOST",
                                            message="Kitchen no longer knows this order's job; its state was lost",
                                            details={"expectedInstanceId": str(current.kitchen_instance_id),
                                                     "observedInstanceId": str(self.kitchen.instance_id)}))
                return

    async def _apply(self, order_id, view):
        order = await self.book.get(order_id)
        changes = {}
        if order.job_id != view.job_id:
            changes["job_id"] = view.job_id
        if order.kitchen_status != view.status:
            changes["kitchen_status"] = view.status
        if view.instance_id is not None and order.kitchen_instance_id != view.instance_id:
            changes["kitchen_instance_id"] = view.instance_id
        if view.reservation_id is not None and order.reservation_id != view.reservation_id:
            changes["reservation_id"] = view.reservation_id
        now = self.book.now()
        if view.status == "COOKED":
            await self.book.advance(order_id, OrderStatus.READY, completed_at=now, **changes)
            return True
        if view.status == "FAILED":
            reason = view.failure_reason or "KITCHEN_FAILED"
            detail = ErrorDetail(code=reason, message="Kitchen reported the job as failed",
                                 details={"dependency": "kitchen", "jobError": view.last_error or {}})
            await self.book.advance(order_id, OrderStatus.FAILED, failure_reason=reason, last_error=detail, completed_at=now, **changes)
            return True
        if view.status == "UNKNOWN":
            reason = view.failure_reason if view.failure_reason in UNKNOWN_REASONS else "FULFILLMENT_UNCONFIRMED"
            detail = ErrorDetail(code=view.failure_reason or reason, message="Kitchen could not establish the job's outcome",
                                 details={"dependency": "kitchen", "jobError": view.last_error or {}})
            await self.book.advance(order_id, OrderStatus.UNKNOWN, failure_reason=reason, last_error=detail, **changes)
            return True
        if view.status == "COOKING" and order.status in (OrderStatus.CHECKING, OrderStatus.RECONCILING):
            await self.book.advance(order_id, OrderStatus.COOKING, **changes)
        elif order.status == OrderStatus.RECONCILING:
            await self.book.advance(order_id, OrderStatus.CHECKING, **changes)
        elif changes:
            await self.book.advance(order_id, **changes)
        return False

    async def _fail(self, order_id, reason, error):
        await self.book.advance(order_id, OrderStatus.FAILED, failure_reason=reason, last_error=error, completed_at=self.book.now())

    def _attempt(self, operation, outcome):
        return Attempt(operation=operation, outcome=outcome, at=self.book.now())
