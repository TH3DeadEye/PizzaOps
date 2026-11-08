"""Watch the restaurant. Three modes, none of which write to the services:

    uv run python -m viewer --mode demo                         # the offline asset showcase
    uv run python -m viewer --mode live                         # poll the three running services
    uv run python -m viewer --mode replay recordings/shift.jsonl

Live and replay share the same world and renderer, so a recording looks like the shift it came from.
Pause and speed change only what this window shows; they do not pause the kitchen.
"""

import argparse
import os
import sys
from pathlib import Path
from uuid import UUID

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from viewer.assets import AssetPack, DEFAULT_ASSET_ROOT
from viewer.feed import DEFAULT_URLS, Feed, Replay
from viewer.render import SIZE, Renderer
from viewer.world import World


def run_demo(argv):
    from viewer.asset_demo import main as demo
    demo(argv)


class Show:
    def __init__(self, source, *, pack, record_path=None, speed=1.0, caption="LIVE"):
        self.source = source
        self.world = World()
        self.renderer = Renderer(pack)
        self.speed = speed
        self.paused = False
        self.caption = caption
        self.record_path = record_path

    def pump(self, dt_ms):
        for update in self.source.drain():
            self.world.apply(update)
        if not self.paused:
            self.world.tick(dt_ms / 1000 * self.speed)
            self.renderer.update(self.world, dt_ms * self.speed)

    def draw(self, canvas, mode):
        self.renderer.draw(canvas, self.world, mode=mode, paused=self.paused, speed=self.speed, caption=self.caption)

    def close(self):
        closer = getattr(self.source, "stop", None)
        if closer is not None:
            closer()


def loop(show, *, mode, headless, frames, screenshot, scale):
    import pygame
    if headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.display.init()
    pygame.font.init()
    if not headless:
        desktop = pygame.display.get_desktop_sizes()[0]
        scale = max(1, min(scale, desktop[0] // SIZE[0], (desktop[1] - 80) // SIZE[1]))
    window = pygame.display.set_mode((SIZE[0] * scale, SIZE[1] * scale))
    pygame.display.set_caption(f"PizzaOps - {mode}")
    canvas = pygame.Surface(SIZE)
    clock = pygame.time.Clock()
    running, frame_count = True, 0
    limit = frames or (180 if headless else 0)
    try:
        while running:
            delta = min(clock.tick(60), 50)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_SPACE:
                        show.paused = not show.paused
                    elif event.key in (pygame.K_EQUALS, pygame.K_PLUS):
                        show.speed = min(8.0, show.speed * 2 if show.speed >= 1 else show.speed * 2)
                        show.speed = 1.0 if 0.9 < show.speed < 1.1 else show.speed
                    elif event.key == pygame.K_MINUS:
                        show.speed = max(0.25, show.speed / 2)
            show.pump(delta)
            show.draw(canvas, mode)
            pygame.transform.scale(canvas, window.get_size(), window)
            pygame.display.flip()
            frame_count += 1
            if limit and frame_count >= limit:
                running = False
            if mode == "replay" and getattr(show.source, "finished", False) and headless:
                running = False
    finally:
        if screenshot:
            Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
            pygame.image.save(window, screenshot)
        show.close()
        pygame.quit()
    print(f"Viewer completed: {frame_count} frames")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("demo", "live", "replay"), default="demo")
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--atlas", action="store_true")
    parser.add_argument("--run-id", help="PIZZAOPS_RUN_ID of the live restaurant")
    parser.add_argument("--order-url", default=DEFAULT_URLS["order"])
    parser.add_argument("--kitchen-url", default=DEFAULT_URLS["kitchen"])
    parser.add_argument("--inventory-url", default=DEFAULT_URLS["inventory"])
    parser.add_argument("--record", type=Path, help="write live updates as JSONL")
    parser.add_argument("--recording", type=Path, help="JSONL file for --mode replay")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--scale", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--poll-s", type=float, default=0.25)
    args, rest = parser.parse_known_args(argv)
    if args.mode == "demo":
        extra = rest[:]
        if args.headless:
            extra.append("--headless")
        if args.frames:
            extra += ["--frames", str(args.frames)]
        if args.screenshot:
            extra += ["--screenshot", str(args.screenshot)]
        extra += ["--scale", str(args.scale), "--assets", str(args.assets)]
        if args.atlas:
            extra.append("--atlas")
        return run_demo(extra) or 0
    import pygame
    if args.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.display.init()
    pygame.font.init()
    pack = AssetPack(args.assets, use_atlas=args.atlas)
    if args.mode == "live":
        run_id = args.run_id or os.environ.get("PIZZAOPS_RUN_ID")
        if not run_id:
            parser.error("live mode needs --run-id or PIZZAOPS_RUN_ID")
        UUID(run_id)
        urls = {"order": args.order_url, "kitchen": args.kitchen_url, "inventory": args.inventory_url}
        source = Feed(urls, run_id, record_path=args.record, poll_s=args.poll_s).start()
        caption = "LIVE"
    else:
        if args.recording is None:
            parser.error("replay mode needs --recording")
        source = Replay(args.recording, speed=args.speed)
        caption = f"REPLAY {args.recording.name}"
    show = Show(source, pack=pack, speed=args.speed, caption=caption)
    return loop(show, mode=args.mode, headless=args.headless, frames=args.frames, screenshot=args.screenshot, scale=args.scale)


if __name__ == "__main__":
    sys.exit(main() or 0)
