"""Builders for the viewer's unit tests. No pygame, no network."""

from uuid import uuid4

from viewer.updates import Event, Snapshot, Status

RUN = "11111111-1111-4111-8111-111111111111"
INSTANCES = {
    "order": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "kitchen": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "inventory": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
}


def event(service, type, sequence, payload, order_id=None, instance=None, entity_version=1):
    instance = instance or INSTANCES[service]
    return Event(service, {
        "schemaVersion": 1, "runId": RUN, "instanceId": instance, "service": service, "sequence": sequence,
        "timestamp": "2026-11-07T00:00:00.000Z", "traceId": str(uuid4()), "orderId": order_id,
        "entityId": order_id or "svc", "entityVersion": entity_version, "type": type, "payload": payload,
    }, received_at=sequence)


def snapshot(service, watermark, **fields):
    instance = fields.pop("instanceId", INSTANCES[service])
    data = {"service": service, "runId": RUN, "instanceId": instance,
            "watermark": {"instanceId": instance, "sequence": watermark}}
    data.update(fields)
    return Snapshot(service, data, received_at=0)


def order_record(order_id, status="RECEIVED", version=1, pizza="CHEESE", quantity=1, appearance="customer_amber",
                 trait="patient", failure=None, sequence=1):
    return {
        "orderId": order_id, "requestId": str(uuid4()),
        "customer": {"id": str(uuid4()), "appearance": appearance, "trait": trait},
        "pizzaType": pizza, "quantity": quantity, "status": status, "failureReason": failure, "lastError": None,
        "jobId": None, "kitchenStatus": None, "kitchenInstanceId": None, "reservationId": None, "attempts": [],
        "sequence": sequence, "version": version, "traceId": str(uuid4()),
        "createdAt": "2026-11-07T00:00:00.000Z", "updatedAt": "2026-11-07T00:00:00.000Z", "completedAt": None,
    }


def job_record(order_id, status="QUEUED", version=1, pizza="CHEESE", quantity=1, failure=None):
    return {
        "jobId": str(uuid4()), "orderId": order_id, "pizzaType": pizza, "quantity": quantity,
        "items": {"DOUGH": quantity, "SAUCE": quantity, "CHEESE": quantity}, "status": status, "result": None,
        "failureReason": failure, "lastError": None, "reservationId": None, "inventoryInstanceId": None,
        "attempts": [], "version": version, "traceId": str(uuid4()),
        "createdAt": "2026-11-07T00:00:00.000Z", "updatedAt": "2026-11-07T00:00:00.000Z",
        "deadlineAt": "2026-11-07T00:00:15.000Z", "startedAt": None, "completedAt": None,
    }


def mood(label="CALM", trust=60, patience=85, version=1):
    return {"patience": patience, "stress": 0, "trust": trust, "failedReservationCount": 0, "mood": label, "version": version}


def live(*names):
    return [Status(name, "LIVE", None, 0.0) for name in names]


def advance(world, seconds, step=0.05):
    remaining = seconds
    while remaining > 0:
        dt = min(step, remaining)
        world.tick(dt)
        remaining -= dt
