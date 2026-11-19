"""Record a shift for offline replay, without a window: the services, the autopilot and a feed recorder.

    uv run python -m tools.record_demo                                   # Friday night -> assets/game/demo/friday.jsonl
    uv run python -m tools.record_demo --scenario calm --seconds 120 --out recordings/calm.jsonl

It uses the same show profile as scripts/play.py and runs in real time, so a five-minute shift takes five minutes.
Watch the result with ``uv run python -m viewer --mode replay --recording <file>``.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from scripts.dev import Launcher
from scripts.play import SHOW_PROFILE
from viewer.feed import Feed

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "assets" / "game" / "demo" / "friday.jsonl"


def record(out, *, scenario="friday", seed=7, seconds=325.0, log_dir=None):
    log_dir = Path(log_dir or ROOT / ".pizzaops" / "record")
    launcher = Launcher(ports={"order": 0, "kitchen": 0, "inventory": 0}, log_dir=log_dir, extra_env=SHOW_PROFILE)
    launcher.start()
    feed = autopilot = None
    try:
        urls = launcher.urls
        feed = Feed(urls, launcher.run_id, record_path=out).start()
        time.sleep(1.0)  # the recording opens on snapshots of an empty restaurant
        with (log_dir / "autopilot.log").open("w") as log:
            autopilot = subprocess.Popen(
                [sys.executable, "-m", "autopilot.run", "--scenario", scenario, "--seed", str(seed), "--run-id", launcher.run_id,
                 "--order-url", urls["order"], "--kitchen-url", urls["kitchen"], "--inventory-url", urls["inventory"]],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                feed.drain()  # the recording is written as updates arrive; keep the queue from overflowing
                time.sleep(0.5)
    finally:
        if autopilot is not None and autopilot.poll() is None:
            autopilot.terminate()
            autopilot.wait(timeout=5)
        if feed is not None:
            feed.stop()
        launcher.stop()
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="friday")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--seconds", type=float, default=325.0, help="how long to record (the scenario plus a short tail)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    print(f"Recording {args.scenario} (seed {args.seed}) for {args.seconds:g}s to {args.out} ...", flush=True)
    record(args.out, scenario=args.scenario, seed=args.seed, seconds=args.seconds)
    print(f"Recorded {sum(1 for _ in args.out.open())} updates to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
