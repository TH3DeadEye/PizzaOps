"""Standalone offline asset showcase. No service requests or simulated outcomes."""

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

from viewer.assets import Animator, AssetPack, DEFAULT_ASSET_ROOT
from viewer.geometry import depth_key, direction_for, move_actor, project, unproject  # noqa: F401  (public helpers)

SIZE = (900, 600)
INK = "#49343b"
CREAM = "#f3dfb6"
CHARACTERS = ["chef", "cashier", "stock_worker", "customer_amber", "customer_teal", "customer_lavender"]
ACTIONS = ["idle", "walk", "work", "carry", "talk", "frustrated"]


class Showcase:
    def __init__(self, pack):
        self.pack = pack
        self.scene = json.loads((pack.root / "scene.json").read_text())
        self.characters = self.scene["characters"]
        self.font = pygame.font.Font(None, 19)
        self.small = pygame.font.Font(None, 16)
        self.title = pygame.font.Font(None, 34)
        self.elapsed = 0.0
        self.paused = False
        self.gallery = False
        self.grid = False
        self.gallery_action = 1
        self.gallery_direction = "se"
        self.gallery_clips = [Animator(pack, f"{name}/walk/se") for name in CHARACTERS]
        for actor in self.characters:
            actor["animator"] = Animator(pack, f"{actor['character']}/{actor['action']}/{actor['direction']}")
            actor["route_index"] = 1
        self.oven = Animator(pack, "oven/lit")

    def update(self, delta_ms):
        if self.paused:
            return
        self.elapsed += delta_ms
        self.oven.update(delta_ms)
        for actor in self.characters:
            move_actor(actor, delta_ms / 1000)
            actor["animator"].play(f"{actor['character']}/{actor['action']}/{actor['direction']}")
            actor["animator"].update(delta_ms)
        for character, animator in zip(CHARACTERS, self.gallery_clips, strict=True):
            action = ACTIONS[self.gallery_action]
            direction = self.gallery_direction
            if action != "walk" and direction in ("ne", "nw"):
                direction = "se" if direction == "ne" else "sw"
            animator.play(f"{character}/{action}/{direction}")
            animator.update(delta_ms)

    def text(self, target, text, point, font=None, color=CREAM):
        target.blit((font or self.font).render(text, True, color), point)

    def draw(self, target):
        target.fill("#302c32")
        pygame.draw.rect(target, "#24272c", (0, 0, SIZE[0], 70))
        self.text(target, "PIZZAOPS", (22, 15), self.title)
        self.text(target, "THE COZY RESTAURANT COLLECTION", (192, 29), self.small, "#c8ad8d")
        self.text(target, "OFFLINE ASSET SHOWCASE", (697, 19), self.small, "#9db59a")
        self.text(target, "6 characters / 4 walk directions", (697, 36), self.small)
        if self.gallery:
            self.draw_gallery(target)
        else:
            self.draw_scene(target)
        pygame.draw.rect(target, "#24272c", (0, 560, SIZE[0], 40))
        self.text(target, "SPACE pause   TAB gallery   G grid   +/- zoom   ESC quit", (20, 570), self.small)
        self.text(target, "PAUSED" if self.paused else "ANIMATING", (802, 570), self.small, "#9db59a")

    def draw_scene(self, target):
        origin = self.scene["origin"]
        for tile in sorted(self.scene["floors"], key=depth_key):
            point = project(tile["position"], origin)
            self.pack.draw(target, tile["sprite"], point)
            if self.grid:
                self.text(target, f"{tile['position'][0]},{tile['position'][1]}", (point[0] - 7, point[1] - 3), self.small, INK)
        for obj in self.scene["objects"]:
            if obj.get("layer") in ("ground", "back"):
                self.pack.draw(target, obj["sprite"], project(obj["position"], origin, obj.get("elevation", 0)))
        drawables = [obj for obj in self.scene["objects"] if obj.get("layer") not in ("ground", "back")]
        drawables += self.characters
        for obj in sorted(drawables, key=depth_key):
            if "animator" in obj:
                sprite = obj["animator"].frame_id
            elif obj.get("animation"):
                sprite = self.oven.frame_id
            else:
                sprite = obj["sprite"]
            self.pack.draw(target, sprite, project(obj["position"], origin, obj.get("elevation", 0)))
        self.pack.draw(target, "ui_panel", (20, 86))
        self.text(target, "LITTLE SLICE / 01", (31, 97), self.font, INK)
        self.text(target, "Kitchen + dining", (31, 121), self.small, INK)
        self.text(target, "64 x 32 modular tiles", (31, 140), self.small, INK)
        self.text(target, "TAB: animations", (31, 158), self.small, INK)
        self.text(target, "Preview movement only. Connect your events when ready.", (20, 538), self.small, "#b8a99b")

    def draw_gallery(self, target):
        action = ACTIONS[self.gallery_action]
        self.text(target, f"ANIMATION STUDIO / {action.upper()} / {self.gallery_direction.upper()}", (24, 89), self.title)
        self.text(target, "LEFT / RIGHT: action       UP / DOWN: direction       TAB: restaurant", (24, 124), self.font, "#b8a99b")
        for index, (character, animator) in enumerate(zip(CHARACTERS, self.gallery_clips, strict=True)):
            left = 20 + index * 146
            pygame.draw.rect(target, "#3b353b", (left, 168, 136, 260), border_radius=5)
            for tile_x in range(8):
                for tile_y in range(10):
                    if (tile_x + tile_y) % 2 == 0:
                        pygame.draw.rect(target, "#403a40", (left + 4 + tile_x * 16, 172 + tile_y * 16, 16, 16))
            self.pack.draw(target, animator.frame_id, (left + 68, 333), 3)
            pygame.draw.line(target, "#bb8762", (left + 8, 335), (left + 128, 335))
            label = character.replace("customer_", "").replace("_", " ").title()
            self.text(target, label, (left + 12, 353), self.font)
            self.text(target, "48 x 64 / feet 24,60", (left + 12, 380), self.small, "#b8a99b")
            self.text(target, "RGBA / isolated", (left + 12, 400), self.small, "#9db59a")
        self.text(target, "Walk: SE / SW / NE / NW. Other actions: SE / SW. West poses are mirrored.", (24, 458), self.font)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--scale", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--atlas", action="store_true")
    parser.add_argument("--gallery", action="store_true")
    args = parser.parse_args(argv)
    if args.frames < 0:
        parser.error("--frames must be nonnegative")
    if args.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.display.init()
    pygame.font.init()
    scale = args.scale
    if not args.headless:
        desktop = pygame.display.get_desktop_sizes()[0]
        scale = max(1, min(scale, desktop[0] // SIZE[0], (desktop[1] - 80) // SIZE[1]))
    window = pygame.display.set_mode((SIZE[0] * scale, SIZE[1] * scale))
    pygame.display.set_caption("PizzaOps - Cozy Asset Showcase")
    canvas = pygame.Surface(SIZE)
    demo = Showcase(AssetPack(args.assets, use_atlas=args.atlas))
    demo.gallery = args.gallery
    clock = pygame.time.Clock()
    running = True
    frame_count = 0
    limit = args.frames or (120 if args.headless else 0)
    while running:
        delta = 1000 / 60 if args.headless else min(clock.tick(60), 100)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_SPACE:
                    demo.paused = not demo.paused
                elif event.key == pygame.K_TAB:
                    demo.gallery = not demo.gallery
                elif event.key == pygame.K_g:
                    demo.grid = not demo.grid
                elif event.key in (pygame.K_RIGHT, pygame.K_LEFT):
                    demo.gallery_action = (demo.gallery_action + (1 if event.key == pygame.K_RIGHT else -1)) % len(ACTIONS)
                elif event.key in (pygame.K_UP, pygame.K_DOWN):
                    directions = ["se", "sw", "nw", "ne"]
                    demo.gallery_direction = directions[(directions.index(demo.gallery_direction) + (1 if event.key == pygame.K_UP else -1)) % 4]
                elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_MINUS):
                    scale = max(1, min(3, scale + (-1 if event.key == pygame.K_MINUS else 1)))
                    window = pygame.display.set_mode((SIZE[0] * scale, SIZE[1] * scale))
        demo.update(delta)
        demo.draw(canvas)
        pygame.transform.scale(canvas, window.get_size(), window)
        pygame.display.flip()
        frame_count += 1
        if limit and frame_count >= limit:
            running = False
    if args.screenshot:
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(window, args.screenshot)
    pygame.quit()
    print(f"Preview completed: {frame_count} frames")


if __name__ == "__main__":
    main()
