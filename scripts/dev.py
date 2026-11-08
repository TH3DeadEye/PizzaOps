"""Launch Inventory, Kitchen and Order as local child processes under one run ID, and stop them together.

    uv run python scripts/dev.py                       # 8082, 8081, 8080; Ctrl-C stops everything
    uv run python scripts/dev.py --cooking-s 1 --env KITCHEN_WORKERS=3

Each service writes its JSON event log to --log-dir. The launcher only starts, checks and stops processes;
it never talks to the business endpoints.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
SERVICE_PREFIXES = ("INVENTORY_", "KITCHEN_", "ORDER_", "PIZZAOPS_")
DEFAULT_PORTS = {"inventory": 8082, "kitchen": 8081, "order": 8080}
DEFAULT_LOG_DIR = ROOT / ".pizzaops" / "logs"


class LaunchError(Exception):
    pass


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def url(port):
    return f"http://127.0.0.1:{port}"


class Service:
    def __init__(self, name, port, environment, log_path):
        self.name = name
        self.port = port
        self.base_url = url(port)
        self.environment = environment
        self.log_path = log_path
        self.process = None
        self._log = None

    def start(self, run_id, ready_timeout):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("w")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", f"services.{self.name}.main:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning", "--no-access-log", "--timeout-graceful-shutdown", "2"],
            cwd=ROOT, env=self.environment, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + ready_timeout
        with httpx.Client(base_url=self.base_url, timeout=0.3, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise LaunchError(f"{self.name} exited with code {self.process.returncode}; see {self.log_path}")
                try:
                    response = client.get("/health")
                except httpx.TransportError:
                    response = None
                if response is not None and response.status_code == 200:
                    if response.json().get("runId") != run_id:
                        raise LaunchError(f"{self.name} on port {self.port} answers for another run; is an old process still running?")
                    return
                time.sleep(0.05)
        raise LaunchError(f"{self.name} did not become ready within {ready_timeout:g}s; see {self.log_path}")

    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout=5):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=timeout)
            self.process = None
        if self._log is not None:
            self._log.close()
            self._log = None


class Launcher:
    """Starts the three services in dependency order and stops them in reverse, whatever happens in between."""

    def __init__(self, run_id=None, ports=None, log_dir=DEFAULT_LOG_DIR, extra_env=None, ready_timeout=15):
        self.run_id = str(run_id or uuid4())
        ports = {**DEFAULT_PORTS, **(ports or {})}
        self.ports = {name: (port or free_port()) for name, port in ports.items()}
        self.log_dir = Path(log_dir)
        self.ready_timeout = ready_timeout
        base = {name: value for name, value in os.environ.items() if not name.startswith(SERVICE_PREFIXES)}
        base["PIZZAOPS_RUN_ID"] = self.run_id
        base.update(extra_env or {})
        wiring = {"inventory": {}, "kitchen": {"KITCHEN_INVENTORY_URL": url(self.ports["inventory"])},
                  "order": {"ORDER_KITCHEN_URL": url(self.ports["kitchen"])}}
        self.services = [Service(name, self.ports[name], {**base, **wiring[name]}, self.log_dir / f"{name}.log")
                         for name in ("inventory", "kitchen", "order")]

    @property
    def urls(self):
        return {service.name: service.base_url for service in self.services}

    def start(self):
        try:
            for service in self.services:
                service.start(self.run_id, self.ready_timeout)
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        for service in reversed(self.services):
            service.stop()

    def crashed(self):
        return [service.name for service in self.services if not service.running()]

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def parse_env(items):
    pairs = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not name:
            raise argparse.ArgumentTypeError(f"--env expects KEY=VALUE, got {item!r}")
        pairs[name] = value
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", type=UUID, help="reuse a run ID (default: a new one)")
    parser.add_argument("--order-port", type=int, default=DEFAULT_PORTS["order"])
    parser.add_argument("--kitchen-port", type=int, default=DEFAULT_PORTS["kitchen"])
    parser.add_argument("--inventory-port", type=int, default=DEFAULT_PORTS["inventory"])
    parser.add_argument("--cooking-s", type=float, help="KITCHEN_COOKING_S for this run")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="extra service setting (repeatable)")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    args = parser.parse_args(argv)
    try:
        extra = parse_env(args.env)
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if args.cooking_s is not None:
        extra["KITCHEN_COOKING_S"] = str(args.cooking_s)
    ports = {"order": args.order_port, "kitchen": args.kitchen_port, "inventory": args.inventory_port}
    launcher = Launcher(args.run_id, ports, args.log_dir, extra)
    stop = {"requested": False}

    def request_stop(signum, frame):
        stop["requested"] = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)
    try:
        launcher.start()
    except LaunchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"PizzaOps is open. run ID {launcher.run_id}")
    for service in launcher.services:
        print(f"  {service.name:<9} {service.base_url}   log {service.log_path}")
    print(f"\nexport PIZZAOPS_RUN_ID={launcher.run_id}")
    print("Ctrl-C closes the restaurant.", flush=True)
    code = 0
    try:
        while not stop["requested"]:
            crashed = launcher.crashed()
            if crashed:
                print(f"error: {', '.join(crashed)} exited unexpectedly; stopping the rest", file=sys.stderr)
                code = 1
                break
            time.sleep(0.25)
    finally:
        launcher.stop()
        print("All services stopped.")
    return code


if __name__ == "__main__":
    sys.exit(main())
