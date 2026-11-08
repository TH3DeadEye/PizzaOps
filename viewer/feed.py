"""Polls the three services and turns their snapshots and events into a stream of updates.

The async ``Poller`` is the whole policy: snapshot first, then events strictly after the watermark, drain
``hasMore`` before the next idle wait, treat 410 as "take a new snapshot", and back off on disconnect. ``Feed``
runs that loop on a background thread and pushes into a bounded queue; if the queue overflows it signals
``Resync`` instead of dropping events quietly. ``Replay`` reads a JSONL recording and yields the same updates
on a clock, so the world cannot tell live from playback.
"""

import asyncio
import json
import queue
import threading
import time
from pathlib import Path

import httpx

from viewer.updates import Event, Resync, SERVICES, Snapshot, Status, dumps, from_record

DEFAULT_URLS = {"order": "http://127.0.0.1:8080", "kitchen": "http://127.0.0.1:8081", "inventory": "http://127.0.0.1:8082"}
POLL_S = 0.25
BACKOFF = (0.25, 0.5, 1.0, 2.0, 4.0)
PAGE = 100
QUEUE = 2000


class ServiceError(Exception):
    def __init__(self, status, code, body):
        super().__init__(code)
        self.status, self.code, self.body = status, code, body


class Client:
    def __init__(self, name, base_url, run_id, *, transport=None, timeout=2.0, connect=0.3):
        self.name = name
        self.run_id = str(run_id) if run_id else None
        self._client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout, connect=connect),
                                         transport=transport, trust_env=False)

    async def aclose(self):
        await self._client.aclose()

    async def snapshot(self):
        return await self._get("/snapshot")

    async def events(self, instance_id, after, limit=PAGE):
        return await self._get("/events", params={"instanceId": str(instance_id), "after": after, "limit": limit})

    async def _get(self, path, params=None):
        headers = {"X-Run-Id": self.run_id} if self.run_id else {}
        try:
            response = await self._client.get(path, headers=headers, params=params)
        except httpx.ConnectError as error:
            raise ConnectionError(f"{self.name} refused the connection") from error
        except httpx.TimeoutException as error:
            raise TimeoutError(f"{self.name} did not answer") from error
        except httpx.HTTPError as error:
            raise ConnectionError(f"{self.name} transport failed: {error}") from error
        if response.status_code >= 400:
            try:
                body = response.json()
                code = str(body.get("code") or f"HTTP_{response.status_code}")
            except ValueError:
                body, code = {"message": response.text[:200]}, f"HTTP_{response.status_code}"
            raise ServiceError(response.status_code, code, body)
        return response.json()


class Cursor:
    def __init__(self):
        self.instance_id = None
        self.sequence = 0
        self.failures = 0
        self.next_at = 0.0
        self.draining = False

    def backoff(self, now):
        wait = BACKOFF[min(self.failures, len(BACKOFF) - 1)]
        self.failures += 1
        self.next_at = now + wait
        return wait

    def ok(self, now, poll_s):
        self.failures = 0
        self.next_at = now if self.draining else now + poll_s


class Poller:
    def __init__(self, urls, run_id, emit, *, transports=None, now=time.monotonic, sleep=asyncio.sleep, poll_s=POLL_S):
        transports = transports or {}
        self.clients = {name: Client(name, urls[name], run_id, transport=transports.get(name)) for name in SERVICES}
        self.emit = emit
        self._now = now
        self._sleep = sleep
        self.poll_s = poll_s
        self.cursors = {name: Cursor() for name in SERVICES}
        self._resync = False

    def request_resync(self):
        self._resync = True
        for cursor in self.cursors.values():
            cursor.next_at = 0.0
            cursor.draining = False

    async def aclose(self):
        await asyncio.gather(*(client.aclose() for client in self.clients.values()))

    async def run(self):
        try:
            while True:
                now = self._now()
                if self._resync:
                    self._resync = False
                    for name in SERVICES:
                        self.cursors[name].instance_id = None
                        self.cursors[name].next_at = 0.0
                waited = False
                for name in SERVICES:
                    cursor = self.cursors[name]
                    if cursor.next_at > now:
                        continue
                    waited = True
                    await self._poll(name)
                if not waited:
                    await self._sleep(min((c.next_at for c in self.cursors.values()), default=now + self.poll_s) - now)
        except asyncio.CancelledError:
            raise
        finally:
            await self.aclose()

    async def _poll(self, name):
        cursor, now = self.cursors[name], self._now()
        try:
            if cursor.instance_id is None:
                await self._snapshot(name)
            else:
                await self._page(name)
        except ServiceError as error:
            if error.code == "RUN_MISMATCH":
                self.emit(Status(name, "RUN_MISMATCH", "This viewer belongs to a different run", now))
                cursor.instance_id = None
                cursor.backoff(now)
            elif error.code in ("EVENT_CURSOR_RESET", "INVALID_CURSOR"):
                cursor.instance_id = None
                cursor.next_at = now
            else:
                self.emit(Status(name, "DISCONNECTED", error.code, now))
                cursor.backoff(now)
        except (ConnectionError, TimeoutError) as error:
            self.emit(Status(name, "DISCONNECTED", str(error), now))
            cursor.backoff(now)

    async def _snapshot(self, name):
        now = self._now()
        self.emit(Status(name, "SYNCING", None, now))
        data = await self.clients[name].snapshot()
        cursor = self.cursors[name]
        cursor.instance_id = data["instanceId"]
        cursor.sequence = data["watermark"]["sequence"]
        cursor.draining = True
        cursor.ok(self._now(), self.poll_s)
        self.emit(Snapshot(name, data, self._now()))
        self.emit(Status(name, "LIVE", None, self._now()))

    async def _page(self, name):
        cursor = self.cursors[name]
        page = await self.clients[name].events(cursor.instance_id, cursor.sequence)
        now = self._now()
        for event in page["events"]:
            self.emit(Event(name, event, now))
            cursor.sequence = event["sequence"]
        cursor.sequence = page["nextCursor"]["sequence"]
        cursor.draining = bool(page["hasMore"])
        cursor.ok(now, self.poll_s)


class Feed:
    """Background thread around a ``Poller``. The render thread only calls ``drain``."""

    def __init__(self, urls, run_id, *, transports=None, record_path=None, poll_s=POLL_S, queue_size=QUEUE, now=time.monotonic):
        self.urls, self.run_id = urls, run_id
        self.transports, self.poll_s, self._now = transports, poll_s, now
        self.queue = queue.Queue(maxsize=queue_size)
        if record_path:
            path = Path(record_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._record = path.open("w")
        else:
            self._record = None
        self._thread = None
        self._loop = None
        self._task = None
        self._poller = None
        self._dropped = False

    def start(self):
        self._thread = threading.Thread(target=self._run, name="pizzaops-feed", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout=2.0):
        if self._loop is not None and self._task is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        if self._record is not None:
            self._record.close()
            self._record = None

    def drain(self):
        updates = []
        while True:
            try:
                updates.append(self.queue.get_nowait())
            except queue.Empty:
                return updates

    def _emit(self, update):
        if self._record is not None:
            self._record.write(dumps(update) + "\n")
            self._record.flush()
        try:
            self.queue.put_nowait(update)
        except queue.Full:
            self._dropped = True
            try:
                while True:
                    self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(Resync(self._now()))
            except queue.Full:
                pass
            if self._poller is not None:
                self._poller.request_resync()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._poller = Poller(self.urls, self.run_id, self._emit, transports=self.transports, now=self._now, poll_s=self.poll_s)
        self._task = self._loop.create_task(self._poller.run())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            self._loop.close()
            self._loop = None
            self._task = None


class Replay:
    """Yield recorded updates when the playback clock reaches their original ``receivedAt`` (shifted to start at 0)."""

    def __init__(self, path, *, speed=1.0, now=time.monotonic):
        self.records = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        self.speed = speed
        self._now = now
        self._origin = None
        self._started = None
        self._index = 0
        if self.records:
            self._origin = self.records[0]["receivedAt"]

    def drain(self):
        if self._origin is None:
            return []
        if self._started is None:
            self._started = self._now()
        elapsed = (self._now() - self._started) * self.speed
        updates = []
        while self._index < len(self.records):
            record = self.records[self._index]
            if record["receivedAt"] - self._origin > elapsed:
                break
            updates.append(from_record(record))
            self._index += 1
        return updates

    @property
    def finished(self):
        return self._index >= len(self.records)
