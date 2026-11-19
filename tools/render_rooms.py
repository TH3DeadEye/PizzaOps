"""Save actual Pygame screenshots of all room views without starting services.

    python -m tools.render_rooms --output assets/pizzaops/previews/rooms
"""

import argparse
import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from viewer.assets import AssetPack, DEFAULT_ASSET_ROOT
from viewer.asset_demo import Showcase
from viewer.layout import ROOM_NAMES
from viewer.scene import SIZE


def render(destination):
    destination.mkdir(parents=True, exist_ok=True)
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    demo = Showcase(AssetPack())
    try:
        for room in ROOM_NAMES:
            demo.select_room(room)
            demo.update(400)
            canvas = pygame.Surface(SIZE)
            demo.draw(canvas)
            path = destination / f"PizzaOps_Room_{room}.png"
            pygame.image.save(canvas, path)
            print(path)
    finally:
        pygame.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_ASSET_ROOT / "previews" / "rooms")
    render(parser.parse_args().output)
