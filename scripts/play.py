"""Open the restaurant and watch a shift: one command starts the three services, the autopilot and the viewer.

    uv run python scripts/play.py                         # Friday night, seed 7
    uv run python scripts/play.py --scenario calm --seed 3
    uv run python scripts/play.py --record recordings/friday.jsonl

The services run with a show profile: a 10-second bake and one cook, so the characters have time to act out every
order, and deadlines long enough for a queue to form without failing. After the scenario a calm evening continues
until you close the window (Esc). Everything stops together.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.dev import DEFAULT_LOG_DIR, LaunchError, Launcher, parse_env  # noqa: E402

SHOW_PROFILE = {
    "KITCHEN_COOKING_S": "10",
    "KITCHEN_WORKERS": "1",
    # Queue time counts against a job's deadline, and Order must keep watching longer than Kitchen keeps trying.
    "KITCHEN_JOB_DEADLINE_S": "180",
    "ORDER_OBSERVE_TIMEOUT_S": "240",
}


def start_autopilot(launcher, args, log_path):
    urls = launcher.urls
    command = [sys.executable, "-m", "autopilot.run", "--scenario", args.scenario, "--seed", str(args.seed),
               "--time-scale", str(args.time_scale), "--loop", "--run-id", launcher.run_id,
               "--order-url", urls["order"], "--kitchen-url", urls["kitchen"], "--inventory-url", urls["inventory"]]
    log = log_path.open("w")
    try:
        return subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT), log
    except BaseException:
        log.close()
        raise


def stop_autopilot(process, log):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if log is not None:
        log.close()


def viewer_arguments(launcher, args):
    urls = launcher.urls
    argv = ["--mode", "live", "--run-id", launcher.run_id, "--order-url", urls["order"],
            "--kitchen-url", urls["kitchen"], "--inventory-url", urls["inventory"], "--scale", str(args.scale)]
    if args.record:
        argv += ["--record", str(args.record)]
    if args.headless:
        argv.append("--headless")
    if args.frames:
        argv += ["--frames", str(args.frames)]
    if args.screenshot:
        argv += ["--screenshot", str(args.screenshot)]
    return argv


def main(argv=None):
    from autopilot.director import SCENARIOS

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="friday")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--record", type=Path, help="also write the shift to a JSONL recording for replay")
    parser.add_argument("--scale", type=int, choices=(1, 2, 3), default=2, help="window scale (capped to your screen)")
    parser.add_argument("--cooking-s", type=float, help="override the show profile's bake time")
    parser.add_argument("--time-scale", type=float, default=1.0, help="run the autopilot's schedule faster")
    parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="extra service setting (repeatable)")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--headless", action="store_true", help="no window: run --frames frames and exit (tests)")
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args(argv)
    try:
        extra = {**SHOW_PROFILE, **parse_env(args.env)}
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if args.cooking_s is not None:
        extra["KITCHEN_COOKING_S"] = str(args.cooking_s)

    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    launcher = Launcher(ports={"order": 0, "kitchen": 0, "inventory": 0}, log_dir=args.log_dir, extra_env=extra)
    try:
        launcher.start()
    except LaunchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    autopilot, log = None, None
    try:
        print(f"PizzaOps is open: {SCENARIOS[args.scenario].summary}")
        print(f"  run ID {launcher.run_id}")
        for service in launcher.services:
            print(f"  {service.name:<9} {service.base_url}   log {service.log_path}")
        autopilot, log = start_autopilot(launcher, args, args.log_dir / "autopilot.log")
        print(f"  autopilot log {args.log_dir / 'autopilot.log'}", flush=True)
        from viewer.main import main as watch
        return watch(viewer_arguments(launcher, args)) or 0
    finally:
        stop_autopilot(autopilot, log)
        launcher.stop()
        print("Closed the restaurant.")


if __name__ == "__main__":
    sys.exit(main())
