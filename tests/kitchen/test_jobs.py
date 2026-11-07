import asyncio
from uuid import UUID, uuid4

import pytest

from services.inventory.settings import DEFAULT_STOCK
from services.kitchen.errors import KitchenError
from services.kitchen.models import JobStatus, ReconcileRequest
from tests.kitchen.harness import FakeClock, Gate, restaurant

pytestmark = pytest.mark.anyio


async def test_happy_path_cooks_and_consumes_exactly_the_recipe():
    async with restaurant() as world:
        trace_id = uuid4()
        job, created = await world.submit("PEPPERONI", 2, trace_id=trace_id)
        assert created and job.status == JobStatus.QUEUED and job.version == 1
        assert job.items == {"DOUGH": 2, "SAUCE": 2, "CHEESE": 2, "PEPPERONI": 2}
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.COOKED and done.result == "COOKED" and done.failure_reason is None
        assert done.reservation_id is not None and done.started_at is not None and done.completed_at is not None
        assert done.inventory_instance_id == world.inventory.instance_id
        assert [attempt.outcome for attempt in done.attempts] == ["SUFFICIENT", "RESERVED"]
        assert await world.stock() == DEFAULT_STOCK | {"DOUGH": 38, "SAUCE": 38, "CHEESE": 28, "PEPPERONI": 13}
        assert await world.event_types(job.order_id) == ["COOK_QUEUED", "COOK_CHECKING", "STOCK_CHECKED", "MOOD_CHANGED",
                                                         "COOK_STARTED", "COOK_COMPLETED"]
        mood = await world.kitchen.mood()
        assert mood.trust == 65 and mood.mood == "CALM"
        assert [(request.method, request.url.path) for request in world.calls()] == [("GET", "/stock"), ("POST", "/reservations")]
        for request in world.calls():
            assert request.headers["X-Trace-Id"] == str(trace_id)
            assert request.headers["X-Run-Id"] == str(world.inventory_settings.run_id)
        inventory_events = (await world.inventory.events(world.inventory.instance_id, 0, 500)).events
        assert any(event.type == "RESERVATION_SUCCEEDED" and event.trace_id == trace_id and event.order_id == job.order_id
                   for event in inventory_events)


async def test_twelve_orders_compete_for_three_cheese_and_marinara_still_cooks():
    async with restaurant(stock={"CHEESE": 3}) as world:
        jobs = [(await world.submit("CHEESE"))[0] for _ in range(12)]
        results = [await world.wait_for(job.order_id) for job in jobs]
        statuses = [job.status for job in results]
        assert statuses.count(JobStatus.COOKED) == 3 and statuses.count(JobStatus.FAILED) == 9
        failed = [job for job in results if job.status == JobStatus.FAILED]
        assert all(job.failure_reason == "INSUFFICIENT_STOCK" for job in failed)
        assert all(job.last_error.code == "INSUFFICIENT_STOCK" and job.last_error.status == 409 for job in failed)
        assert all(job.last_error.details["missing"] == {"CHEESE": 1} for job in failed)
        assert all(job.completed_at is not None and job.reservation_id is None for job in failed)
        stock = await world.stock()
        assert stock["CHEESE"] == 0 and stock["DOUGH"] == 37 and stock["SAUCE"] == 37
        marinara, _ = await world.submit("MARINARA", 2)
        assert (await world.wait_for(marinara.order_id)).status == JobStatus.COOKED
        mood = await world.kitchen.mood()
        assert mood.failed_reservation_count == 9 and mood.trust == 60 + 5 * 4
        assert "RELATIONSHIP_CHANGED" not in await world.event_types()


async def test_duplicate_submission_is_idempotent_and_conflicts_are_rejected():
    async with restaurant() as world:
        order_id = uuid4()
        first, created = await world.submit("CHEESE", 1, order_id=order_id)
        again, created_again = await world.submit("CHEESE", 1, order_id=order_id)
        assert created and not created_again and again.job_id == first.job_id
        with pytest.raises(KitchenError) as caught:
            await world.submit("CHEESE", 2, order_id=order_id)
        assert caught.value.status == 409 and caught.value.code == "IDEMPOTENCY_CONFLICT"
        await world.wait_for(order_id)
        replay, created_late = await world.submit("CHEESE", 1, order_id=order_id)
        assert not created_late and replay.status == JobStatus.COOKED and replay.job_id == first.job_id
        assert (await world.stock())["CHEESE"] == 29
        assert len(world.calls("POST", "/reservations")) == 1


async def test_queue_full_rejects_admission_without_creating_a_job():
    gate = Gate()
    async with restaurant(workers=1, queue_capacity=1, cooking_s=1, sleep=gate.sleep) as world:
        cooking, _ = await world.submit()
        await asyncio.wait_for(gate.entered.wait(), 2)
        waiting, _ = await world.submit()
        with pytest.raises(KitchenError) as caught:
            await world.submit()
        assert caught.value.status == 503 and caught.value.code == "QUEUE_FULL"
        assert caught.value.headers == {"Retry-After": "1"}
        snapshot = await world.kitchen.snapshot()
        assert len(snapshot.jobs) == 2 and snapshot.queue.depth == 1 and snapshot.queue.capacity == 1
        again, created = await world.submit(order_id=waiting.order_id)
        assert not created and again.job_id == waiting.job_id
        gate.release.set()
        assert (await world.wait_for(cooking.order_id)).status == JobStatus.COOKED
        assert (await world.wait_for(waiting.order_id)).status == JobStatus.COOKED


async def test_unknown_pizza_and_quantity_limit_create_nothing():
    async with restaurant(max_quantity=2) as world:
        with pytest.raises(KitchenError) as caught:
            await world.submit("HAWAIIAN")
        assert (caught.value.status, caught.value.code) == (400, "UNKNOWN_PIZZA")
        with pytest.raises(KitchenError) as caught:
            await world.submit("CHEESE", 3)
        assert (caught.value.status, caught.value.code) == (400, "INVALID_REQUEST")
        assert (await world.kitchen.snapshot()).jobs == [] and world.calls() == []


async def test_lost_response_after_allocation_is_reconciled_without_double_deduction():
    async with restaurant() as world:
        world.transport.plan("POST", "/reservations", "lose_response")
        job, _ = await world.submit("CHEESE", 1)
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.COOKED and done.reservation_id is not None
        assert [attempt.outcome for attempt in done.attempts] == ["SUFFICIENT", "TIMEOUT", "FOUND"]
        assert done.last_error.code == "TIMEOUT" and done.last_error.details["operation"] == "reserve"
        assert (await world.stock())["CHEESE"] == 29
        reservations = (await world.inventory.snapshot()).reservations
        assert len(reservations) == 1 and reservations[0].reservation_id == done.reservation_id
        assert await world.event_types(job.order_id) == [
            "COOK_QUEUED", "COOK_CHECKING", "STOCK_CHECKED", "DEPENDENCY_TIMEOUT", "MOOD_CHANGED", "COOK_RECONCILING",
            "MOOD_CHANGED", "COOK_STARTED", "COOK_COMPLETED"]
        assert [(request.method, request.url.path) for request in world.calls()] == [
            ("GET", "/stock"), ("POST", "/reservations"), ("GET", f"/reservations/{job.order_id}")]


async def test_lost_request_before_delivery_is_resubmitted_under_the_same_key():
    async with restaurant() as world:
        world.transport.plan("POST", "/reservations", "lose_request")
        job, _ = await world.submit("CHEESE", 1)
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.COOKED
        assert [attempt.outcome for attempt in done.attempts] == ["SUFFICIENT", "TIMEOUT", "NOT_FOUND", "RESERVED"]
        posts = world.calls("POST", "/reservations")
        assert len(posts) == 2 and posts[0].content == posts[1].content
        assert (await world.stock())["CHEESE"] == 29
        assert len((await world.inventory.snapshot()).reservations) == 1


async def test_inventory_refusing_connections_fails_cleanly_after_one_retry():
    async with restaurant() as world:
        world.transport.plan("GET", "/stock", "refuse")
        world.transport.plan("POST", "/reservations", "refuse", "refuse", "refuse")
        job, _ = await world.submit("CHEESE", 1)
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.FAILED and done.failure_reason == "INVENTORY_UNAVAILABLE"
        assert done.last_error.code == "CONNECTION_FAILED" and done.last_error.status is None
        assert [attempt.outcome for attempt in done.attempts] == ["CONNECTION_FAILED"] * 3
        assert len(world.calls("POST", "/reservations")) == 2
        assert (await world.event_types(job.order_id)).count("DEPENDENCY_UNAVAILABLE") == 3
        mood = await world.kitchen.mood()
        assert mood.patience == 70 and mood.stress == 30 and mood.trust == 60
        assert (await world.stock())["CHEESE"] == 30


async def test_inventory_503_before_mutation_is_a_known_failure_and_recovers():
    async with restaurant() as world:
        await world.set_faults(failureMode="UNAVAILABLE")
        job, _ = await world.submit("CHEESE", 1)
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.FAILED and done.failure_reason == "INVENTORY_UNAVAILABLE"
        assert done.last_error.code == "INVENTORY_UNAVAILABLE" and done.last_error.status == 503
        assert len(world.calls("POST", "/reservations")) == 2
        await world.set_faults()
        again, _ = await world.submit("CHEESE", 1)
        assert (await world.wait_for(again.order_id)).status == JobStatus.COOKED
        assert (await world.stock())["CHEESE"] == 29


async def test_stale_stock_contradiction_lowers_trust_and_the_next_order_demands_authoritative_stock():
    async with restaurant() as world:
        await world.set_faults(staleStockEnabled=True)
        await world.reserve_elsewhere({"CHEESE": 30})
        job, _ = await world.submit("CHEESE", 1)
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.FAILED and done.failure_reason == "INSUFFICIENT_STOCK"
        types = await world.event_types(job.order_id)
        assert types == ["COOK_QUEUED", "COOK_CHECKING", "STOCK_CHECKED", "RELATIONSHIP_CHANGED", "COOK_FAILED"]
        relationship = next(event for event in await world.events() if event.type == "RELATIONSHIP_CHANGED")
        assert relationship.payload.claimed["CHEESE"] == 30 and relationship.payload.missing == {"CHEESE": 1}
        assert (relationship.payload.snapshot_version, relationship.payload.stock_version) == (1, 2)
        assert (relationship.payload.before.trust, relationship.payload.after.trust) == (60, 35)
        mood = await world.kitchen.mood()
        assert (mood.trust, mood.patience, mood.mood, mood.failed_reservation_count) == (35, 75, "SUSPICIOUS", 1)
        checks = [event for event in await world.events() if event.type == "STOCK_CHECKED"]
        assert checks[0].payload.authoritative is False and checks[0].payload.source == "CACHED" and checks[0].payload.sufficient
        marinara, _ = await world.submit("MARINARA", 1)
        assert (await world.wait_for(marinara.order_id)).status == JobStatus.COOKED
        checks = [event for event in await world.events() if event.type == "STOCK_CHECKED"]
        assert checks[1].payload.authoritative is True and checks[1].payload.source == "AUTHORITATIVE"
        assert world.calls("GET", "/stock")[1].url.params["authoritative"] == "true"


async def test_concurrent_consumption_after_a_cached_read_is_a_shortage_not_a_lie():
    async with restaurant(stock={"CHEESE": 1}) as world:
        await world.set_faults(staleStockEnabled=True)

        async def take_the_last_cheese():
            await world.reserve_elsewhere({"CHEESE": 1})

        world.transport.plan("POST", "/reservations", take_the_last_cheese)
        job, _ = await world.submit("CHEESE", 1)
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.FAILED and done.failure_reason == "INSUFFICIENT_STOCK"
        types = await world.event_types(job.order_id)
        assert "RELATIONSHIP_CHANGED" not in types and "MOOD_CHANGED" in types
        mood = await world.kitchen.mood()
        assert (mood.trust, mood.failed_reservation_count, mood.stress) == (60, 1, 10)


async def test_inventory_restart_during_reconciliation_reports_state_lost():
    async with restaurant() as world:
        before = world.inventory.instance_id
        world.transport.plan("POST", "/reservations", "lose_response")
        world.transport.plan("GET", "/reservations/", world.restart_inventory)
        job, _ = await world.submit("CHEESE", 1)
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.UNKNOWN and done.failure_reason == "DEPENDENCY_STATE_LOST"
        assert done.result is None and done.completed_at is None and done.inventory_instance_id == before
        assert UUID(done.last_error.details["expectedInstanceId"]) == before
        assert UUID(done.last_error.details["observedInstanceId"]) == world.inventory.instance_id != before
        assert len(world.calls("POST", "/reservations")) == 1
        assert (await world.stock())["CHEESE"] == 30
        assert (await world.event_types(job.order_id))[-1] == "COOK_UNKNOWN"


async def test_reconciliation_budget_exhaustion_reports_unknown_not_failed():
    async with restaurant(reconcile_attempts=2) as world:
        world.transport.plan("POST", "/reservations", *["lose_request"] * 10)
        world.transport.plan("GET", "/reservations/", *["lose_request"] * 10)
        job, _ = await world.submit("CHEESE", 1)
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.UNKNOWN and done.failure_reason == "FULFILLMENT_UNCONFIRMED"
        assert done.last_error.details["rounds"] == 2 and done.result is None and done.completed_at is None
        types = await world.event_types(job.order_id)
        assert types.count("DEPENDENCY_TIMEOUT") == 3 and types[-1] == "COOK_UNKNOWN"
        assert (await world.stock())["CHEESE"] == 30


async def test_deadline_expiry_while_queued_fails_without_touching_inventory():
    clock, gate = FakeClock(), Gate()
    async with restaurant(workers=1, job_deadline_s=1, cooking_s=1, clock=clock, sleep=gate.sleep) as world:
        first, _ = await world.submit()
        await asyncio.wait_for(gate.entered.wait(), 2)
        second, _ = await world.submit()
        clock.advance(2)
        gate.release.set()
        assert (await world.wait_for(first.order_id)).status == JobStatus.COOKED
        late = await world.wait_for(second.order_id)
        assert late.status == JobStatus.FAILED and late.failure_reason == "COOK_DEADLINE_EXCEEDED"
        assert late.attempts == [] and late.reservation_id is None and len(world.calls()) == 2
        assert (await world.stock())["CHEESE"] == 29


async def test_reconcile_command_changes_mood_only_and_is_deduplicated():
    async with restaurant() as world:
        world.transport.plan("GET", "/stock", "refuse")
        world.transport.plan("POST", "/reservations", "refuse", "refuse")
        job, _ = await world.submit()
        await world.wait_for(job.order_id)
        worn = await world.kitchen.mood()
        assert (worn.patience, worn.stress) == (70, 30)
        command = ReconcileRequest(command_id=uuid4())
        first = await world.kitchen.reconcile(command, uuid4())
        assert first.before == worn and (first.mood.patience, first.mood.trust, first.mood.stress) == (85, 70, 10)
        assert await world.kitchen.reconcile(command, uuid4()) == first
        assert (await world.kitchen.mood()).version == first.mood.version
        assert await world.stock() == DEFAULT_STOCK
        reconciled = [event for event in await world.events() if event.type == "MOOD_CHANGED" and event.payload.reason == "RECONCILE"]
        assert len(reconciled) == 1 and reconciled[0].payload.command_id == command.command_id


async def test_snapshot_watermark_and_event_paging():
    async with restaurant(event_capacity=4) as world:
        snapshot = await world.kitchen.snapshot()
        assert snapshot.watermark.sequence == 1 and snapshot.queue.workers == 2 and snapshot.queue.accepting
        assert snapshot.recipes["MARINARA"] == {"DOUGH": 1, "SAUCE": 1, "BASIL": 1}
        job, _ = await world.submit()
        await world.wait_for(job.order_id)
        page = await world.kitchen.events(world.kitchen.instance_id, 3, 2)
        assert [event.sequence for event in page.events] == [4, 5] and page.has_more
        with pytest.raises(KitchenError) as caught:
            await world.kitchen.events(world.kitchen.instance_id, 0, 100)
        assert caught.value.code == "EVENT_CURSOR_RESET" and caught.value.status == 410
        with pytest.raises(KitchenError) as caught:
            await world.kitchen.events(uuid4(), 6, 100)
        assert caught.value.status == 410
        with pytest.raises(KitchenError) as caught:
            await world.kitchen.events(world.kitchen.instance_id, 99, 100)
        assert caught.value.code == "INVALID_CURSOR"
        final = await world.kitchen.snapshot()
        assert final.jobs[0].status == JobStatus.COOKED and final.watermark.sequence == 7
        assert final.inventory.instance_id == world.inventory.instance_id


async def test_worker_survives_internal_errors_and_reports_unknown():
    async with restaurant() as world:
        original = world.kitchen.inventory.reserve

        async def explode(*args, **kwargs):
            raise RuntimeError("boom")

        world.kitchen.inventory.reserve = explode
        job, _ = await world.submit()
        done = await world.wait_for(job.order_id)
        assert done.status == JobStatus.UNKNOWN and done.failure_reason == "KITCHEN_INTERNAL_ERROR"
        assert done.last_error.code == "INTERNAL_ERROR" and done.last_error.message == "boom"
        world.kitchen.inventory.reserve = original
        again, _ = await world.submit()
        assert (await world.wait_for(again.order_id)).status == JobStatus.COOKED


async def test_stop_drains_queued_work_and_refuses_new_jobs():
    world = restaurant()
    async with world:
        jobs = [(await world.submit())[0] for _ in range(5)]
    assert {(await world.kitchen.job(job.order_id)).status for job in jobs} == {JobStatus.COOKED}
    with pytest.raises(KitchenError) as caught:
        await world.submit()
    assert caught.value.code == "KITCHEN_UNAVAILABLE" and caught.value.status == 503
    types = [event.type for event in (await world.kitchen.events(world.kitchen.instance_id, 0, 500)).events]
    assert types[0] == "SERVICE_STARTED" and types[-1] == "SERVICE_STOPPING"
