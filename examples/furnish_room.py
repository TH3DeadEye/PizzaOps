"""Preview your own JSON layout: python -m examples.furnish_room --layout examples/my_room.json"""

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from viewer.assets import AssetPack
from viewer.scene import SceneView

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, default=ROOT / "examples" / "my_room.json")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if args.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.display.init()
    pygame.font.init()
    window = pygame.display.set_mode((1000, 700))
    pygame.display.set_caption("PizzaOps / Your room")
    scene = json.loads(args.layout.read_text())
    view = SceneView(AssetPack(), scene, pygame.Rect(16, 76, 968, 564))
    title, font = pygame.font.Font(None, 30), pygame.font.Font(None, 20)
    clock, frames, running = pygame.time.Clock(), 0, True
    limit = args.frames or (30 if args.headless else 0)
    while running:
        dt = 1000 / 60 if args.headless else clock.tick(60)
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                running = False
        view.oven.update(dt)
        window.fill("#302c32")
        window.blit(title.render("YOUR ROOM / BUILD IT YOUR WAY", True, "#f3dfb6"), (24, 18))
        window.blit(font.render(f"Layout: {args.layout.name}", True, "#c8ad8d"), (24, 48))
        view.draw(window)
        window.blit(font.render("Edit sprite IDs and positions in the JSON, then restart. ESC closes the preview.", True, "#c8ad8d"), (24, 665))
        pygame.display.flip()
        frames += 1
        if limit and frames >= limit:
            running = False
    if args.screenshot:
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(window, args.screenshot)
    pygame.quit()
    print(f"Room preview completed: {frames} frames")


if __name__ == "__main__":
    main()
