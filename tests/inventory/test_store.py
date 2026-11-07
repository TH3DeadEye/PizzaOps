import asyncio
from uuid import uuid4

import pytest

from services.inventory.errors import InventoryError
from services.inventory.models import FaultRequest, ReservationRequest, RestockRequest
from services.inventory.settings import DEFAULT_STOCK, Settings
from services.inventory.store import InventoryStore


def reserve_request(items=None, order_id=None):
    return ReservationRequest(order_id=order_id or uuid4(), items=items or {"CHEESE": 1})


def fault_request(**overrides):
    return FaultRequest(command_id=uuid4(), **{
        "stale_stock_enabled": False, "delay_ms": 0, "failure_mode": "NONE", "delay_phase": "BEFORE_MUTATION", **overrides})


def test_twelve_requests_compete_for_three_units():
    async def scenario():
        store = InventoryStore(Settings(initial_stock=DEFAULT_STOCK | {"CHEESE": 3}))
        results = await asyncio.gather(*(store.reserve(reserve_request(), uuid4()) for _ in range(12)), return_exceptions=True)
        assert sum(not isinstance(result, Exception) for result in results) == 3
        failures = [result for result in results if isinstance(result, InventoryError)]
        assert len(failures) == 9 and all(result.code == "INSUFFICIENT_STOCK" for result in failures)
        snapshot = await store.snapshot()
        assert snapshot.items["CHEESE"] == 0
        assert snapshot.stock_version == 4
        assert len(snapshot.reservations) == 3
    asyncio.run(scenario())


def test_concurrent_duplicates_and_conflicting_payload():
    async def scenario():
        store = InventoryStore(Settings())
        request = reserve_request({"CHEESE": 2, "DOUGH": 2})
        results = await asyncio.gather(*(store.reserve(request, uuid4()) for _ in range(12)))
        assert len({result.reservation_id for result in results}) == 1
        assert all(result == results[0] for result in results)
        with pytest.raises(InventoryError, match="different ingredients"):
            await store.reserve(reserve_request({"CHEESE": 3}, request.order_id), uuid4())
        snapshot = await store.snapshot()
        assert snapshot.items == DEFAULT_STOCK | {"CHEESE": 28, "DOUGH": 38}
        assert snapshot.watermark.sequence == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("items, code", [({"DOUGH": 2, "CHEESE": 31}, "INSUFFICIENT_STOCK"),
                                         ({"DOUGH": 2, "MYSTERY": 1}, "UNKNOWN_INGREDIENT"),
                                         ({"CHEESE": 10_001}, "INVALID_REQUEST")])
def test_reservation_failure_never_partially_deducts(items, code):
    async def scenario():
        store = InventoryStore(Settings())
        with pytest.raises(InventoryError) as caught:
            await store.reserve(reserve_request(items), uuid4())
        assert caught.value.code == code
        snapshot = await store.snapshot()
        assert snapshot.items == DEFAULT_STOCK
        assert snapshot.stock_version == 1 and snapshot.reservations == []
    asyncio.run(scenario())


def test_shortage_can_retry_after_restock_and_results_are_detached():
    async def scenario():
        store = InventoryStore(Settings(initial_stock=DEFAULT_STOCK | {"CHEESE": 0}))
        request = reserve_request()
        with pytest.raises(InventoryError):
            await store.reserve(request, uuid4())
        await store.restock(RestockRequest(command_id=uuid4(), items={"CHEESE": 2}), uuid4())
        result = await store.reserve(request, uuid4())
        result.items["CHEESE"] = 999
        snapshot = await store.snapshot()
        snapshot.items["CHEESE"] = 999
        snapshot.reservations[0].items["CHEESE"] = 999
        replay = await store.reserve(request, uuid4())
        assert replay.items == {"CHEESE": 1}
        assert (await store.snapshot()).items["CHEESE"] == 1
    asyncio.run(scenario())


def test_admin_commands_deduplicate_and_conflict_across_operations():
    async def scenario():
        store = InventoryStore(Settings())
        command = RestockRequest(command_id=uuid4(), items={"CHEESE": 5})
        responses = await asyncio.gather(*(store.restock(command, uuid4()) for _ in range(10)))
        assert all(response == responses[0] for response in responses)
        assert (await store.snapshot()).items["CHEESE"] == 35
        with pytest.raises(InventoryError) as caught:
            await store.set_faults(fault_request().model_copy(update={"command_id": command.command_id}), uuid4())
        assert caught.value.code == "IDEMPOTENCY_CONFLICT"
        assert (await store.snapshot()).fault_version == 0
    asyncio.run(scenario())


def test_restock_limits_are_atomic():
    async def scenario():
        store = InventoryStore(Settings(stock_limit=40))
        with pytest.raises(InventoryError) as caught:
            await store.restock(RestockRequest(command_id=uuid4(), items={"CHEESE": 1, "DOUGH": 1}), uuid4())
        assert caught.value.code == "STOCK_LIMIT_EXCEEDED"
        assert (await store.snapshot()).items == DEFAULT_STOCK
        result = await store.restock(RestockRequest(command_id=uuid4(), items={"CHEESE": 10}), uuid4())
        assert result.items["CHEESE"] == 40
    asyncio.run(scenario())


def test_stale_snapshot_is_frozen_and_reservations_use_real_stock():
    async def scenario():
        store = InventoryStore(Settings(initial_stock=DEFAULT_STOCK | {"CHEESE": 1}))
        command = fault_request(stale_stock_enabled=True)
        original = await store.set_faults(command, uuid4())
        await store.reserve(reserve_request(), uuid4())
        assert await store.set_faults(command, uuid4()) == original
        await store.set_faults(fault_request(stale_stock_enabled=True), uuid4())
        stale = await store.stock(uuid4(), "CHEESE")
        real = await store.stock(uuid4(), "CHEESE", authoritative=True)
        assert stale.items["CHEESE"] == 1 and stale.source == "CACHED"
        assert stale.snapshot_version == 1 and stale.stock_version == 2
        assert real.items["CHEESE"] == 0 and real.snapshot_version == 2
        with pytest.raises(InventoryError) as caught:
            await store.reserve(reserve_request(), uuid4())
        assert caught.value.code == "INSUFFICIENT_STOCK"
        await store.set_faults(fault_request(), uuid4())
        await store.set_faults(fault_request(stale_stock_enabled=True), uuid4())
        assert (await store.stock(uuid4())).snapshot_version == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("phase, expected", [("BEFORE_MUTATION", 30), ("AFTER_MUTATION_BEFORE_RESPONSE", 29)])
def test_cancelled_response_and_retry_preserve_exactly_one_allocation(phase, expected):
    async def scenario():
        entered = asyncio.Event()
        async def blocked_sleep(seconds):
            assert seconds == 1
            entered.set()
            await asyncio.Event().wait()
        store = InventoryStore(Settings(), sleep=blocked_sleep)
        await store.set_faults(fault_request(delay_ms=1000, delay_phase=phase), uuid4())
        request = reserve_request()
        pending = asyncio.create_task(store.reserve(request, uuid4()))
        await asyncio.wait_for(entered.wait(), 1)
        snapshot = await asyncio.wait_for(store.snapshot(), 1)
        assert snapshot.items["CHEESE"] == expected
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await store.set_faults(fault_request(), uuid4())
        result = await store.reserve(request, uuid4())
        assert result == await store.reservation(request.order_id)
        assert (await store.snapshot()).items["CHEESE"] == 29
    asyncio.run(scenario())


def test_existing_reservation_bypasses_injected_unavailability():
    async def scenario():
        store = InventoryStore(Settings())
        request = reserve_request()
        result = await store.reserve(request, uuid4())
        await store.set_faults(fault_request(failure_mode="UNAVAILABLE"), uuid4())
        assert await store.reserve(request, uuid4()) == result
        with pytest.raises(InventoryError) as caught:
            await store.reserve(reserve_request(), uuid4())
        assert caught.value.status == 503
        assert (await store.snapshot()).items["CHEESE"] == 29
    asyncio.run(scenario())


def test_snapshot_watermark_event_paging_and_retention():
    async def scenario():
        store = InventoryStore(Settings(event_capacity=3))
        await store.lifecycle(True)
        snapshot = await store.snapshot()
        for _ in range(3):
            await store.reserve(reserve_request(), uuid4())
        page = await store.events(store.instance_id, snapshot.watermark.sequence, 2)
        assert [event.sequence for event in page.events] == [2, 3] and page.has_more
        final = await store.events(store.instance_id, page.next_cursor.sequence, 2)
        assert [event.sequence for event in final.events] == [4] and not final.has_more
        with pytest.raises(InventoryError) as caught:
            await store.events(store.instance_id, 0, 100)
        assert caught.value.code == "EVENT_CURSOR_RESET"
        with pytest.raises(InventoryError) as caught:
            await store.events(uuid4(), 4, 100)
        assert caught.value.status == 410
        with pytest.raises(InventoryError) as caught:
            await store.events(store.instance_id, 5, 100)
        assert caught.value.code == "INVALID_CURSOR"
    asyncio.run(scenario())
