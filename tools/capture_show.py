"""Replay a recording headless with a fixed time step and save screenshots at chosen moments.

    uv run python -m tools.capture_show --at 10,60,150,240
    uv run python -m tools.capture_show --at 140 --zoom 2 --focus 6,4 --out assets/game/previews --name standoff

The same recording, times and options always give the same pictures (no wall clock, seeded effects).
"""

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from viewer.assets import AssetPack  # noqa: E402
from viewer.feed import Replay  # noqa: E402
from viewer.geometry import project  # noqa: E402
from viewer.main import Playback, Show  # noqa: E402
from viewer.render import SIZE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORDING = ROOT / "assets" / "game" / "demo" / "friday.jsonl"


def capture(recording, times, out, *, zoom=1, focus=None, step=1 / 30, name=None):
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode(SIZE)
    playback = Playback()
    show = Show(Replay(recording, now=playback), pack=AssetPack(), caption=f"REPLAY {Path(recording).name}", playback=playback)
    camera = show.renderer.camera
    if zoom > 1:
        camera.zoom = max(camera.min_zoom, min(camera.max_zoom, zoom))
        camera.center = camera._clamped(project(focus) if focus else camera.center)
        camera.target = list(camera.center)
    canvas = pygame.Surface(SIZE)
    out.mkdir(parents=True, exist_ok=True)
    saved, pending = [], sorted(times)
    while pending:
        show.pump(step * 1000)
        if playback() >= pending[0]:
            moment = pending.pop(0)
            show.draw(canvas, "replay")
            path = out / (f"{name}.png" if name and len(times) == 1 else f"{name or 'show'}_{moment:06.1f}.png")
            pygame.image.save(canvas, path)
            saved.append(path)
    pygame.quit()
    return saved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recording", type=Path, default=DEFAULT_RECORDING)
    parser.add_argument("--at", required=True, help="comma-separated seconds into the recording")
    parser.add_argument("--out", type=Path, default=ROOT / ".pizzaops" / "captures")
    parser.add_argument("--zoom", type=int, choices=(1, 2), default=1)
    parser.add_argument("--focus", help="tile to centre when zoomed, e.g. 4,10")
    parser.add_argument("--name", help="file name stem")
    args = parser.parse_args(argv)
    times = [float(value) for value in args.at.split(",") if value.strip()]
    focus = tuple(float(value) for value in args.focus.split(",")) if args.focus else None
    for path in capture(args.recording, times, args.out, zoom=args.zoom, focus=focus, name=args.name):
        print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
