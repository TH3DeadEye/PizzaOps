"""Launch real service processes for acceptance tests and stop them reliably."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE_PREFIXES = ("INVENTORY_", "KITCHEN_", "ORDER_", "PIZZAOPS_")


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def clean_environment(**overrides):
    environment = {name: value for name, value in os.environ.items() if not name.startswith(SERVICE_PREFIXES)}
    environment.update({name: str(value) for name, value in overrides.items()})
    return environment


class ServiceProcess:
    def __init__(self, name, module, port, log_path, environment, run_id):
        self.name = name
        self.module = module
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.log_path = log_path
        self.environment = environment
        self.run_id = run_id
        self.process = None
        self._log = None

    def start(self, ready_timeout=15):
        self._log = self.log_path.open("w")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", f"{self.module}:app", "--host", "127.0.0.1", "--port", str(self.port),
             "--log-level", "warning", "--no-access-log", "--timeout-graceful-shutdown", "2"],
            cwd=ROOT, env=self.environment, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + ready_timeout
        with httpx.Client(base_url=self.base_url, timeout=0.3, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    pytest.fail(f"{self.name} exited during startup:\n{self.log_path.read_text()}")
                try:
                    response = client.get("/health")
                    if response.status_code == 200:
                        assert response.json()["runId"] == self.run_id
                        return self
                except httpx.TransportError:
                    pass
                time.sleep(0.025)
        pytest.fail(f"{self.name} did not become ready:\n{self.log_path.read_text()}")

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def log_lines(self):
        return self.log_path.read_text().splitlines()
