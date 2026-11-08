"""Messages from the feed to the world, and their JSONL form for recording and replay."""

import json
from dataclasses import dataclass

SERVICES = ("order", "kitchen", "inventory")
STATES = ("DISCONNECTED", "SYNCING", "LIVE", "RUN_MISMATCH")


@dataclass(frozen=True)
class Snapshot:
    service: str
    data: dict
    received_at: float


@dataclass(frozen=True)
class Event:
    service: str
    data: dict
    received_at: float


@dataclass(frozen=True)
class Status:
    """Connection state of one service as seen by the feed; ``detail`` is a short human-readable reason."""

    service: str
    state: str
    detail: str | None
    received_at: float


@dataclass(frozen=True)
class Resync:
    """The feed dropped updates (its queue overflowed); fresh snapshots follow. The world should not trust gaps."""

    received_at: float


KINDS = {Snapshot: "snapshot", Event: "event", Status: "status", Resync: "resync"}


def to_record(update):
    record = {"kind": KINDS[type(update)], "receivedAt": update.received_at}
    if isinstance(update, (Snapshot, Event)):
        record.update(service=update.service, data=update.data)
    elif isinstance(update, Status):
        record.update(service=update.service, state=update.state, detail=update.detail)
    return record


def from_record(record):
    kind = record["kind"]
    if kind == "snapshot":
        return Snapshot(record["service"], record["data"], record["receivedAt"])
    if kind == "event":
        return Event(record["service"], record["data"], record["receivedAt"])
    if kind == "status":
        return Status(record["service"], record["state"], record.get("detail"), record["receivedAt"])
    if kind == "resync":
        return Resync(record["receivedAt"])
    raise ValueError(f"Unknown record kind: {kind!r}")


def dumps(update):
    return json.dumps(to_record(update), separators=(",", ":"))


def loads(line):
    return from_record(json.loads(line))
