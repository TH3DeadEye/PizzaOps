import asyncio
from uuid import UUID, uuid4

import httpx
import pytest

from services.inventory.settings import DEFAULT_STOCK
from services.order.errors import OrderError
from services.order.models import OrderRequest, OrderStatus
from services.order.orders import IllegalTransition, OrderBook, decode_cursor, encode_cursor
from services.order.settings import Settings
from tests.order.harness import Gate, customer, restaurant

pytestmark = pytest.mark.anyio


async def test_order_reaches_ready_only_through_a_cooked_job_and_one_trace_crosses_three_services():
    async with restaurant() as world:
        trace_id = uuid4()
        order, created = await world.place("PEPPERONI", 2, trace_id=trace_id)
        assert created and order.status == OrderStatus.RECEIVED and order.version == 1 and order.sequence == 1
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.READY and done.failure_reason is None and done.completed_at is not None
        assert done.job_id is not None and done.reservation_id is not None and done.kitchen_status == "COOKED"
        assert done.kitchen_instance_id == world.kitchen.instance_id
        assert done.trace_id == trace_id
        assert await world.stock() == DEFAULT_STOCK | {"DOUGH": 38, "SAUCE": 38, "CHEESE": 28, "PEPPERONI": 13}
        types = await world.event_types(order.order_id)
        assert types[:2] == ["ORDER_RECEIVED", "ORDER_CHECKING"] and types[-1] == "ORDER_READY"
        assert set(types) <= {"ORDER_RECEIVED", "ORDER_CHECKING", "ORDER_COOKING", "ORDER_READY"}
        for request in world.calls():
            assert request.headers["X-Trace-Id"] == str(trace_id)
            assert request.headers["X-Run-Id"] == str(world.order_settings.run_id)
        kitchen_events = (await world.kitchen.events(world.kitchen.instance_id, 0, 500)).events
        inventory_events = (await world.inventory.events(world.inventory.instance_id, 0, 500)).events
        assert {event.type for event in kitchen_events if event.trace_id == trace_id} >= {"COOK_QUEUED", "COOK_COMPLETED"}
        assert {event.type for event in inventory_events if event.trace_id == trace_id} == {"STOCK_READ", "RESERVATION_SUCCEEDED"}
        job = await world.kitchen.job(order.order_id)
        assert job.job_id == done.job_id and job.reservation_id == done.reservation_id


async def test_cooking_is_observed_when_the_kitchen_is_slow_enough_to_see():
    gate = Gate()
    async with restaurant(kitchen={"cooking_s": 1}, kitchen_sleep=gate.sleep) as world:
        order, _ = await world.place()
        cooking = await world.wait_for(order.order_id, OrderStatus.COOKING)
        assert cooking.kitchen_status == "COOKING" and cooking.reservation_id is not None
        gate.release.set()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.READY
        assert await world.event_types(order.order_id) == ["ORDER_RECEIVED", "ORDER_CHECKING", "ORDER_COOKING", "ORDER_READY"]


async def test_shortage_becomes_a_known_failure_with_the_kitchen_reason():
    async with restaurant(stock={"CHEESE": 0}) as world:
        order, _ = await world.place("CHEESE", 1)
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.FAILED and done.failure_reason == "INSUFFICIENT_STOCK"
        assert done.last_error.code == "INSUFFICIENT_STOCK" and done.kitchen_status == "FAILED"
        assert done.last_error.details["jobError"]["code"] == "INSUFFICIENT_STOCK"
        assert done.last_error.details["jobError"]["details"]["missing"] == {"CHEESE": 1}
        assert (await world.event_types(order.order_id))[-1] == "ORDER_FAILED"
        marinara, _ = await world.place("MARINARA", 1)
        assert (await world.wait_for(marinara.order_id)).status == OrderStatus.READY


async def test_admission_rejects_unknown_pizza_and_quantity_without_calling_the_kitchen():
    async with restaurant(max_quantity=2) as world:
        with pytest.raises(OrderError) as caught:
            await world.place("HAWAIIAN")
        assert (caught.value.status, caught.value.code) == (400, "UNKNOWN_PIZZA")
        assert caught.value.details["menu"] == ["CHEESE", "PEPPERONI", "MARINARA"]
        with pytest.raises(OrderError) as caught:
            await world.place("CHEESE", 3)
        assert (caught.value.status, caught.value.code) == (400, "INVALID_REQUEST")
        assert (await world.service.snapshot()).orders == [] and world.calls() == []


async def test_duplicate_requests_return_the_original_order_and_conflicts_are_rejected():
    async with restaurant() as world:
        request_id, who = uuid4(), customer("chatty", "customer_teal")
        first, created = await world.place("CHEESE", 1, request_id=request_id, who=who)
        again, created_again = await world.place("CHEESE", 1, request_id=request_id, who=who)
        assert created and not created_again and again.order_id == first.order_id and again.trace_id == first.trace_id
        with pytest.raises(OrderError) as caught:
            await world.place("CHEESE", 2, request_id=request_id, who=who)
        assert caught.value.status == 409 and caught.value.code == "IDEMPOTENCY_CONFLICT"
        assert caught.value.details == {"orderId": str(first.order_id)}
        with pytest.raises(OrderError):
            await world.place("CHEESE", 1, request_id=request_id, who=customer("patient", "customer_teal"))
        done = await world.wait_for(first.order_id)
        replay, _ = await world.place("CHEESE", 1, request_id=request_id, who=who)
        assert replay.status == OrderStatus.READY and replay.order_id == done.order_id
        assert len(world.calls("POST", "/cook")) == 1 and (await world.stock())["CHEESE"] == 29


async def test_kitchen_queue_full_is_retried_once_then_a_known_failure():
    gate = Gate()
    async with restaurant(workers=3, kitchen={"workers": 1, "queue_capacity": 1, "cooking_s": 1}, kitchen_sleep=gate.sleep) as world:
        first, _ = await world.place()
        await asyncio.wait_for(gate.entered.wait(), 2)
        second, _ = await world.place()
        await world.wait_until(lambda: _kitchen_status_is(world, second.order_id, "QUEUED"), message="second order queued in kitchen")
        third, _ = await world.place()
        rejected = await world.wait_for(third.order_id)
        assert rejected.status == OrderStatus.FAILED and rejected.failure_reason == "KITCHEN_QUEUE_FULL"
        assert rejected.last_error.code == "QUEUE_FULL" and rejected.last_error.status == 503
        assert [attempt.outcome for attempt in rejected.attempts] == ["QUEUE_FULL", "QUEUE_FULL"]
        assert (await world.event_types(third.order_id)).count("DEPENDENCY_UNAVAILABLE") == 2
        gate.release.set()
        assert (await world.wait_for(first.order_id)).status == OrderStatus.READY
        assert (await world.wait_for(second.order_id)).status == OrderStatus.READY


async def _kitchen_status_is(world, order_id, status):
    return (await world.book.order(order_id)).kitchen_status == status


async def test_kitchen_refusing_connections_is_a_clean_known_failure():
    async with restaurant() as world:
        world.transport.plan("POST", "/cook", "refuse", "refuse", "refuse")
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.FAILED and done.failure_reason == "KITCHEN_UNAVAILABLE"
        assert done.last_error.code == "CONNECTION_FAILED" and done.job_id is None
        assert len(world.calls("POST", "/cook")) == 2
        assert (await world.stock())["CHEESE"] == 30


async def test_lost_submission_response_is_reconciled_to_the_same_job():
    async with restaurant() as world:
        world.transport.plan("POST", "/cook", "lose_response")
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.READY
        assert [attempt.outcome for attempt in done.attempts] == ["TIMEOUT", "FOUND"]
        assert done.last_error.code == "TIMEOUT" and done.last_error.details["operation"] == "submit"
        types = await world.event_types(order.order_id)
        assert types[:4] == ["ORDER_RECEIVED", "ORDER_CHECKING", "DEPENDENCY_TIMEOUT", "ORDER_RECONCILING"] and types[-1] == "ORDER_READY"
        assert len(world.calls("POST", "/cook")) == 1
        assert len((await world.kitchen.snapshot()).jobs) == 1 and (await world.stock())["CHEESE"] == 29


async def test_lost_submission_request_is_resubmitted_under_the_same_order_id():
    async with restaurant() as world:
        world.transport.plan("POST", "/cook", "lose_request")
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.READY
        assert [attempt.outcome for attempt in done.attempts] == ["TIMEOUT", "NOT_FOUND", "ACCEPTED"]
        posts = world.calls("POST", "/cook")
        assert len(posts) == 2 and posts[0].content == posts[1].content
        assert len((await world.kitchen.snapshot()).jobs) == 1 and (await world.stock())["CHEESE"] == 29


async def test_kitchen_restart_during_reconciliation_reports_state_lost():
    async with restaurant() as world:
        warm_up, _ = await world.place()
        await world.wait_for(warm_up.order_id, OrderStatus.READY)
        before = world.kitchen.instance_id
        assert world.service.kitchen.instance_id == before
        world.transport.plan("POST", "/cook", "lose_response")
        world.transport.plan("GET", "/cook/", world.restart_kitchen)
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.UNKNOWN and done.failure_reason == "DEPENDENCY_STATE_LOST"
        assert done.completed_at is None and done.kitchen_instance_id == before
        assert UUID(done.last_error.details["expectedInstanceId"]) == before
        assert UUID(done.last_error.details["observedInstanceId"]) == world.kitchen.instance_id != before
        assert len(world.calls("POST", "/cook")) == 2
        assert (await world.event_types(order.order_id))[-1] == "ORDER_UNKNOWN"


async def test_known_rejection_during_reconciliation_keeps_the_uncertainty():
    async with restaurant() as world:
        async def wrong_run():
            return httpx.Response(409, json={"code": "RUN_MISMATCH", "message": "Request belongs to another run",
                                             "traceId": str(uuid4()), "details": {}},
                                  headers={"X-Instance-Id": str(world.kitchen.instance_id)})

        world.transport.plan("POST", "/cook", "lose_response")
        world.transport.plan("GET", "/cook/", wrong_run)
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.UNKNOWN and done.failure_reason == "FULFILLMENT_UNCONFIRMED"
        assert done.last_error.code == "RUN_MISMATCH" and done.last_error.details["operation"] == "lookup"
        assert [attempt.outcome for attempt in done.attempts] == ["TIMEOUT", "RUN_MISMATCH"]
        assert len(world.calls("POST", "/cook")) == 1 and (await world.stock())["CHEESE"] == 29
        assert (await world.event_types(order.order_id))[-1] == "ORDER_UNKNOWN"


async def test_unobserved_kitchen_restart_falls_back_to_the_same_key_and_never_double_reserves():
    async with restaurant() as world:
        world.transport.plan("POST", "/cook", "lose_response")
        world.transport.plan("GET", "/cook/", world.restart_kitchen)
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.READY
        assert [attempt.outcome for attempt in done.attempts] == ["TIMEOUT", "NOT_FOUND", "ACCEPTED"]
        assert (await world.stock())["CHEESE"] == 29
        assert len((await world.inventory.snapshot()).reservations) == 1


async def test_kitchen_restart_while_cooking_reports_state_lost_instead_of_a_verdict():
    gate = Gate()
    async with restaurant(kitchen={"cooking_s": 1}, kitchen_sleep=gate.sleep) as world:
        order, _ = await world.place()
        cooking = await world.wait_for(order.order_id, OrderStatus.COOKING)
        before = cooking.kitchen_instance_id
        await world.restart_kitchen()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.UNKNOWN and done.failure_reason == "DEPENDENCY_STATE_LOST"
        assert UUID(done.last_error.details["expectedInstanceId"]) == before != world.kitchen.instance_id
        assert done.reservation_id is not None
        assert (await world.stock())["CHEESE"] == 29


async def test_observation_window_exhaustion_reports_unknown_not_failed():
    gate = Gate()
    async with restaurant(observe_timeout_s=0.2, kitchen={"cooking_s": 1}, kitchen_sleep=gate.sleep) as world:
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.UNKNOWN and done.failure_reason == "FULFILLMENT_UNCONFIRMED"
        assert done.last_error.details["lastKitchenStatus"] == "COOKING" and done.completed_at is None
        gate.release.set()
        await world.wait_until(lambda: _job_is(world, order.order_id, "COOKED"), message="kitchen finishing the job")
        assert (await world.book.order(order.order_id)).status == OrderStatus.UNKNOWN


async def _job_is(world, order_id, status):
    return (await world.kitchen.job(order_id)).status == status


async def test_kitchen_unknown_job_becomes_an_unknown_order_with_the_kitchen_reason():
    async with restaurant() as world:
        async def explode(*args, **kwargs):
            raise RuntimeError("stockroom door jammed")

        world.kitchen.inventory.reserve = explode
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.UNKNOWN and done.failure_reason == "FULFILLMENT_UNCONFIRMED"
        assert done.last_error.code == "KITCHEN_INTERNAL_ERROR" and done.kitchen_status == "UNKNOWN"
        assert done.last_error.details["jobError"]["message"] == "stockroom door jammed"


async def test_worker_survives_internal_errors_and_keeps_serving():
    async with restaurant() as world:
        original = world.service.kitchen.submit

        async def explode(*args, **kwargs):
            raise RuntimeError("boom")

        world.service.kitchen.submit = explode
        order, _ = await world.place()
        done = await world.wait_for(order.order_id)
        assert done.status == OrderStatus.UNKNOWN and done.failure_reason == "FULFILLMENT_UNCONFIRMED"
        assert done.last_error.code == "INTERNAL_ERROR" and done.last_error.message == "boom"
        world.service.kitchen.submit = original
        again, _ = await world.place()
        assert (await world.wait_for(again.order_id)).status == OrderStatus.READY


async def test_listing_is_newest_first_with_a_stable_opaque_cursor():
    async with restaurant() as world:
        orders = [(await world.place())[0] for _ in range(5)]
        page = await world.book.list(2)
        assert [order.order_id for order in page.orders] == [orders[4].order_id, orders[3].order_id]
        assert page.next_cursor is not None and decode_cursor(page.next_cursor) == 4
        newer, _ = await world.place()
        second = await world.book.list(2, page.next_cursor)
        assert [order.sequence for order in second.orders] == [3, 2]
        last = await world.book.list(2, second.next_cursor)
        assert [order.sequence for order in last.orders] == [1] and last.next_cursor is None
        assert (await world.book.list(100)).orders[0].order_id == newer.order_id
        for cursor in ("not-base64!", encode_cursor(0), encode_cursor("x")):
            with pytest.raises(OrderError) as caught:
                await world.book.list(2, cursor)
            assert caught.value.code == "INVALID_CURSOR"
        with pytest.raises(OrderError):
            await world.book.list(0)
        with pytest.raises(OrderError) as caught:
            await world.book.order(uuid4())
        assert caught.value.status == 404 and caught.value.code == "ORDER_NOT_FOUND"


async def test_order_book_enforces_legal_transitions():
    book = OrderBook(Settings())
    await book.open()
    order, _ = await book.submit(OrderRequest(request_id=uuid4(), customer=customer(), pizza_type="CHEESE", quantity=1), uuid4())
    with pytest.raises(IllegalTransition):
        await book.advance(order.order_id, OrderStatus.READY)
    with pytest.raises(IllegalTransition):
        await book.advance(order.order_id, OrderStatus.COOKING)
    checking = await book.advance(order.order_id, OrderStatus.CHECKING)
    assert checking.version == 2
    same = await book.advance(order.order_id, OrderStatus.CHECKING, kitchen_status="QUEUED")
    assert same.version == 3 and same.status == OrderStatus.CHECKING
    ready = await book.advance(order.order_id, OrderStatus.READY)
    with pytest.raises(IllegalTransition):
        await book.advance(order.order_id, OrderStatus.FAILED)
    types = [event.type for event in (await book.events(book.instance_id, 0, 100)).events]
    assert types == ["SERVICE_STARTED", "ORDER_RECEIVED", "ORDER_CHECKING", "ORDER_READY"]
    assert ready.version == 4


async def test_stop_drains_queued_orders_and_refuses_new_ones():
    world = restaurant()
    async with world:
        orders = [(await world.place())[0] for _ in range(5)]
    assert {(await world.book.order(order.order_id)).status for order in orders} == {OrderStatus.READY}
    with pytest.raises(OrderError) as caught:
        await world.place()
    assert caught.value.code == "ORDER_UNAVAILABLE" and caught.value.status == 503
    types = await world.event_types()
    assert types[0] == "SERVICE_STARTED" and types[-1] == "SERVICE_STOPPING"
