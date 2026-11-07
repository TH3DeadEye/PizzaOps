"""Cooking jobs: bounded queue, supervised workers, reservation policy and reconciliation.

State changes happen under one ``asyncio.Lock``; the lock is never held across a network call or a sleep.
Each transition bumps the job version and appends exactly one event whose payload is the full job record, so the
log line, the event feed and the viewer all see the same thing.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from services.kitchen import mood as moods
from services.kitchen.errors import KitchenError
from services.kitchen.events import (
    CheckingEvent, CompletedEvent, CookStartedEvent, DependencyPayload, FailedEvent, Journal, LOGGER, MoodEvent,
    MoodPayload, QueuedEvent, ReconcilingEvent, RelationshipEvent, RelationshipPayload, ServicePayload, StartedEvent,
    StockCheckPayload, StockCheckedEvent, StoppingEvent, TimeoutEvent, UnavailableEvent, UnknownEvent,
)
from services.kitchen.inventory_client import DependencyError, DependencyLost, DependencyRejected, DependencyUnavailable
from services.kitchen.models import (
    Attempt, DependencyState, ErrorDetail, Job, JobStatus, QueueState, ReconcileResponse, Snapshot, TERMINAL_STATUSES,
)
from services.kitchen.recipes import RECIPES, expand, shortfall

REJECTION_REASONS = {
    "INSUFFICIENT_STOCK": "INSUFFICIENT_STOCK",
    "UNKNOWN_INGREDIENT": "RECIPE_CONFIGURATION_ERROR",
    "IDEMPOTENCY_CONFLICT": "RESERVATION_CONFLICT",
    "RUN_MISMATCH": "DEPENDENCY_RUN_MISMATCH",
}


class Unconfirmed(Exception):
    def __init__(self, reason, error):
        super().__init__(reason)
        self.reason = reason
        self.error = error


@dataclass(frozen=True)
class StockClaim:
    items: dict[str, int]
    source: str
    stock_version: int
    snapshot_version: int


def _int_map(value):
    try:
        return {str(name): int(quantity) for name, quantity in dict(value).items()}
    except (TypeError, ValueError, AttributeError):
        return {}


class Kitchen:
    def __init__(self, settings, inventory, *, clock=None, sleep=asyncio.sleep):
        self.settings = settings
        self.inventory = inventory
        self.instance_id = uuid4()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._jobs: dict[UUID, Job] = {}
        self._commands: dict[UUID, ReconcileResponse] = {}
        self._mood = moods.initial(settings.initial_patience, settings.initial_trust)
        self._queue: asyncio.Queue[UUID] = asyncio.Queue(maxsize=settings.queue_capacity)
        self._workers: list[asyncio.Task] = []
        self._accepting = False
        self._lock = asyncio.Lock()
        self._journal = Journal(settings.run_id, self.instance_id, settings.event_capacity, clock)

    # ----------------------------------------------------------------- lifecycle

    async def start(self):
        async with self._lock:
            self._accepting = True
            self._journal.append(StartedEvent, uuid4(), "kitchen", 1, ServicePayload(status="UP"))
        self._workers = [asyncio.create_task(self._worker(), name=f"kitchen-worker-{index}")
                         for index in range(self.settings.workers)]

    async def stop(self):
        async with self._lock:
            self._accepting = False
        try:
            await asyncio.wait_for(self._queue.join(), self.settings.drain_timeout_s)
        except TimeoutError:
            pass
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        async with self._lock:
            self._journal.append(StoppingEvent, uuid4(), "kitchen", 1, ServicePayload(status="STOPPING"))
        await self.inventory.aclose()

    # ----------------------------------------------------------------- public API

    async def submit(self, request, trace_id):
        items = expand(request.pizza_type, request.quantity)
        if request.quantity > self.settings.max_quantity:
            raise KitchenError(400, "INVALID_REQUEST", "Quantity exceeds the per-order limit",
                               {"maxQuantity": self.settings.max_quantity})
        async with self._lock:
            existing = self._jobs.get(request.order_id)
            if existing is not None:
                if (existing.pizza_type, existing.quantity) != (request.pizza_type, request.quantity):
                    raise KitchenError(409, "IDEMPOTENCY_CONFLICT", "Order already submitted with a different pizza or quantity",
                                       {"pizzaType": existing.pizza_type, "quantity": existing.quantity})
                return existing.model_copy(deep=True), False
            if not self._accepting:
                raise KitchenError(503, "KITCHEN_UNAVAILABLE", "Kitchen is not accepting new jobs",
                                   {"retryAfterSeconds": 1}, {"Retry-After": "1"})
            if self._queue.full():
                raise KitchenError(503, "QUEUE_FULL", "Kitchen backlog is full",
                                   {"capacity": self.settings.queue_capacity, "retryAfterSeconds": 1}, {"Retry-After": "1"})
            now = self._clock()
            job = Job(job_id=uuid4(), order_id=request.order_id, pizza_type=request.pizza_type, quantity=request.quantity,
                      items=items, status=JobStatus.QUEUED, version=1, trace_id=trace_id, created_at=now, updated_at=now,
                      deadline_at=now + timedelta(seconds=self.settings.job_deadline_s))
            self._jobs[job.order_id] = job
            self._queue.put_nowait(job.order_id)
            self._journal.append(QueuedEvent, trace_id, job.order_id, job.version, job.model_copy(deep=True), job.order_id)
            return job.model_copy(deep=True), True

    async def job(self, order_id):
        async with self._lock:
            job = self._jobs.get(order_id)
            if job is None:
                raise KitchenError(404, "COOK_JOB_NOT_FOUND", "No cooking job exists for this order")
            return job.model_copy(deep=True)

    async def mood(self):
        async with self._lock:
            return self._mood

    async def reconcile(self, request, trace_id):
        async with self._lock:
            existing = self._commands.get(request.command_id)
            if existing is not None:
                return existing
            before = self._mood
            after = self._shift_mood(moods.reconcile, "RECONCILE", trace_id, command_id=request.command_id,
                                     patience_gain=self.settings.reconcile_patience, trust_gain=self.settings.reconcile_trust)
            response = ReconcileResponse(command_id=request.command_id, before=before, mood=after, trace_id=trace_id)
            self._commands[request.command_id] = response
            return response

    async def snapshot(self):
        async with self._lock:
            return Snapshot(run_id=self.settings.run_id, instance_id=self.instance_id,
                            jobs=[job.model_copy(deep=True) for job in self._jobs.values()], mood=self._mood,
                            queue=QueueState(capacity=self.settings.queue_capacity, depth=self._queue.qsize(),
                                             workers=len(self._workers), accepting=self._accepting),
                            inventory=DependencyState(url=self.settings.inventory_url, instance_id=self.inventory.instance_id),
                            recipes={name: dict(recipe) for name, recipe in RECIPES.items()}, watermark=self._journal.cursor())

    async def events(self, instance_id, after, limit):
        async with self._lock:
            return self._journal.page(instance_id, after, limit)

    # ----------------------------------------------------------------- state helpers (caller holds the lock)

    def _bump(self, order_id, *, attempt=None, **changes):
        job = self._jobs[order_id]
        if attempt is not None:
            changes["attempts"] = [*job.attempts, attempt]
        job = job.model_copy(update={**changes, "version": job.version + 1, "updated_at": self._clock()})
        self._jobs[order_id] = job
        return job

    def _emit(self, event_class, job):
        self._journal.append(event_class, job.trace_id, job.order_id, job.version, job.model_copy(deep=True), job.order_id)

    def _shift_mood(self, transition, reason, trace_id, order_id=None, command_id=None, **arguments):
        before = self._mood
        after = transition(before, **arguments)
        if after.version != before.version:
            self._mood = after
            self._journal.append(MoodEvent, trace_id, "mood", after.version,
                                 MoodPayload(reason=reason, before=before, after=after, command_id=command_id), order_id)
        return after

    async def _update(self, order_id, event_class, *, attempt=None, **changes):
        async with self._lock:
            job = self._bump(order_id, attempt=attempt, **changes)
            if event_class is not None:
                self._emit(event_class, job)
            return job

    async def _note(self, order_id, operation, outcome):
        return await self._update(order_id, None, attempt=Attempt(operation=operation, outcome=outcome, at=self._clock()))

    async def _trouble(self, order_id, error, attempt):
        event_class = TimeoutEvent if isinstance(error, DependencyLost) else UnavailableEvent
        payload = DependencyPayload(operation=error.operation, attempt=attempt, code=error.code, message=error.message,
                                    status=error.status)
        async with self._lock:
            job = self._bump(order_id, attempt=Attempt(operation=error.operation, outcome=error.code, at=self._clock()),
                             last_error=error.detail())
            self._journal.append(event_class, job.trace_id, order_id, job.version, payload, order_id)
            self._shift_mood(moods.dependency_trouble, "DEPENDENCY_TROUBLE", job.trace_id, order_id)

    def _expired(self, job):
        return self._clock() >= job.deadline_at

    # ----------------------------------------------------------------- workers

    async def _worker(self):
        while True:
            order_id = await self._queue.get()
            try:
                await self._process(order_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.exception("Kitchen worker failed; orderId=%s", order_id)
                try:
                    await self._abandon(order_id, error)
                except Exception:
                    LOGGER.exception("Kitchen could not record the failure; orderId=%s", order_id)
            finally:
                self._queue.task_done()

    async def _abandon(self, order_id, error):
        async with self._lock:
            job = self._jobs[order_id]
            if job.status in TERMINAL_STATUSES or job.status == JobStatus.UNKNOWN:
                return
            detail = ErrorDetail(code="INTERNAL_ERROR", message=str(error) or type(error).__name__)
            self._emit(UnknownEvent, self._bump(order_id, status=JobStatus.UNKNOWN, failure_reason="KITCHEN_INTERNAL_ERROR",
                                                last_error=detail))

    async def _process(self, order_id):
        async with self._lock:
            job = self._jobs[order_id]
        if job.status != JobStatus.QUEUED:
            return
        if self._expired(job):
            await self._fail(order_id, "COOK_DEADLINE_EXCEEDED", ErrorDetail(
                code="COOK_DEADLINE_EXCEEDED", message="The job deadline passed while the order was queued"))
            return
        job = await self._update(order_id, CheckingEvent, status=JobStatus.CHECKING)
        claim = await self._check_stock(job)
        if self._expired(job):
            await self._fail(order_id, "COOK_DEADLINE_EXCEEDED", ErrorDetail(
                code="COOK_DEADLINE_EXCEEDED", message="The job deadline passed before ingredients were reserved"))
            return
        try:
            reservation = await self._reserve(job, claim)
        except DependencyRejected as rejected:
            await self._reject(job, rejected, claim)
        except DependencyUnavailable as unavailable:
            await self._fail(order_id, "INVENTORY_UNAVAILABLE", unavailable.detail())
        except Unconfirmed as unconfirmed:
            await self._update(order_id, UnknownEvent, status=JobStatus.UNKNOWN, failure_reason=unconfirmed.reason,
                               last_error=unconfirmed.error)
        else:
            await self._cook(job, reservation)

    async def _check_stock(self, job):
        async with self._lock:
            authoritative = moods.demands_authoritative(self._mood)
        try:
            view = await self.inventory.stock(job.trace_id, authoritative=authoritative)
        except DependencyError as error:
            await self._trouble(job.order_id, error, 1)
            return None
        missing = shortfall(view.items, job.items)
        payload = StockCheckPayload(authoritative=authoritative, source=view.source, sufficient=not missing, needed=job.items,
                                    available=view.items, missing=missing, stock_version=view.stock_version,
                                    snapshot_version=view.snapshot_version)
        async with self._lock:
            job = self._bump(job.order_id, attempt=Attempt(operation="stock", outcome="SUFFICIENT" if not missing else "SHORT",
                                                           at=self._clock()))
            self._journal.append(StockCheckedEvent, job.trace_id, job.order_id, job.version, payload, job.order_id)
        return StockClaim(view.items, view.source, view.stock_version, view.snapshot_version)

    async def _reserve(self, job, claim):
        attempt = 0
        while True:
            attempt += 1
            try:
                reservation = await self.inventory.reserve(job.order_id, job.items, job.trace_id)
            except DependencyUnavailable as error:
                await self._trouble(job.order_id, error, attempt)
                if attempt > 1 or self._expired(job):
                    raise
                await self._sleep(self.settings.retry_backoff_s)
            except DependencyLost as error:
                await self._trouble(job.order_id, error, attempt)
                return await self._reconcile(job, attempt)
            except DependencyRejected as error:
                await self._note(job.order_id, "reserve", error.code)
                raise
            else:
                await self._note(job.order_id, "reserve", "RESERVED")
                return reservation

    async def _reconcile(self, job, attempt):
        expected = job.inventory_instance_id or self.inventory.instance_id
        await self._update(job.order_id, ReconcilingEvent, status=JobStatus.RECONCILING, inventory_instance_id=expected)
        started = self._clock()
        window = timedelta(seconds=self.settings.reconcile_window_s)
        rounds = 0
        for rounds in range(1, self.settings.reconcile_attempts + 1):
            await self._sleep(self.settings.reconcile_backoff_s * 2 ** (rounds - 1))
            if self._clock() - started >= window:
                break
            attempt += 1
            try:
                found = await self.inventory.reservation(job.order_id, job.trace_id)
            except (DependencyLost, DependencyUnavailable) as trouble:
                await self._trouble(job.order_id, trouble, attempt)
                continue
            observed = self.inventory.instance_id
            if expected is not None and observed is not None and observed != expected:
                raise Unconfirmed("DEPENDENCY_STATE_LOST", ErrorDetail(
                    code="DEPENDENCY_STATE_LOST", status=None,
                    message="Inventory restarted while the reservation outcome was unknown; its earlier state is gone",
                    details={"expectedInstanceId": str(expected), "observedInstanceId": str(observed)}))
            if found is not None:
                await self._note(job.order_id, "lookup", "FOUND")
                return found
            await self._note(job.order_id, "lookup", "NOT_FOUND")
            if self._expired(job):
                continue
            attempt += 1
            try:
                reservation = await self.inventory.reserve(job.order_id, job.items, job.trace_id)
            except (DependencyLost, DependencyUnavailable) as trouble:
                await self._trouble(job.order_id, trouble, attempt)
                continue
            await self._note(job.order_id, "reserve", "RESERVED")
            return reservation
        raise Unconfirmed("FULFILLMENT_UNCONFIRMED", ErrorDetail(
            code="FULFILLMENT_UNCONFIRMED",
            message="Could not confirm whether ingredients were reserved within the reconciliation window",
            details={"rounds": rounds, "windowSeconds": self.settings.reconcile_window_s}))

    async def _reject(self, job, rejected, claim):
        reason = REJECTION_REASONS.get(rejected.code, "INVENTORY_REJECTED")
        async with self._lock:
            if rejected.code == "INSUFFICIENT_STOCK":
                contradiction = (claim is not None and claim.source == "CACHED" and not shortfall(claim.items, job.items)
                                 and rejected.details.get("stockVersion") == claim.stock_version)
                if contradiction:
                    before = self._mood
                    after = moods.stale_contradiction(before)
                    self._mood = after
                    self._journal.append(RelationshipEvent, job.trace_id, "mood", after.version, RelationshipPayload(
                        before=before, after=after, needed=job.items, claimed=claim.items,
                        missing=_int_map(rejected.details.get("missing")), snapshot_version=claim.snapshot_version,
                        stock_version=claim.stock_version), job.order_id)
                else:
                    self._shift_mood(moods.shortage, "RESERVATION_REJECTED", job.trace_id, job.order_id)
            self._emit(FailedEvent, self._bump(job.order_id, status=JobStatus.FAILED, failure_reason=reason,
                                               last_error=rejected.detail(), completed_at=self._clock()))

    async def _fail(self, order_id, reason, error):
        await self._update(order_id, FailedEvent, status=JobStatus.FAILED, failure_reason=reason, last_error=error,
                           completed_at=self._clock())

    async def _cook(self, job, reservation):
        async with self._lock:
            self._shift_mood(moods.reservation_succeeded, "RESERVATION_SUCCEEDED", job.trace_id, job.order_id)
            self._emit(CookStartedEvent, self._bump(job.order_id, status=JobStatus.COOKING,
                                                    reservation_id=reservation.reservation_id,
                                                    inventory_instance_id=reservation.instance_id, started_at=self._clock()))
        if self.settings.cooking_s:
            await self._sleep(self.settings.cooking_s)
        await self._update(job.order_id, CompletedEvent, status=JobStatus.COOKED, result="COOKED", completed_at=self._clock())
