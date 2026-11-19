"""Browse four-view furniture: python assets/pizzaops_directional/preview.py."""

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--object", default="service_counter")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if args.headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.display.init()
    pygame.font.init()
    window = pygame.display.set_mode((1200, 680))
    pygame.display.set_caption("PizzaOps / Directional furniture")
    manifest = json.loads((ROOT / "manifest.json").read_text())
    names = list(manifest["objects"])
    selected = names.index(args.object) if args.object in names else 0
    images = {name: pygame.image.load(ROOT / entry["file"]).convert_alpha() for name, entry in manifest["sprites"].items()}
    title, label = pygame.font.Font(None, 34), pygame.font.Font(None, 24)
    clock, frame, running = pygame.time.Clock(), 0, True
    limit = args.frames or (2 if args.headless else 0)
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key in (pygame.K_RIGHT, pygame.K_d):
                    selected = (selected + 1) % len(names)
                elif event.key in (pygame.K_LEFT, pygame.K_a):
                    selected = (selected - 1) % len(names)
        name = names[selected]
        obj = manifest["objects"][name]
        window.fill("#302c32")
        window.blit(title.render(name.replace("_", " ").upper(), True, "#f3dfb6"), (26, 22))
        window.blit(label.render(f"{selected + 1}/{len(names)} objects   |   Left / Right: browse   |   Esc: close", True, "#ccb494"), (26, 61))
        for i, direction in enumerate(manifest["directions"]):
            sprite_id = obj["variants"][direction]
            entry, image = manifest["sprites"][sprite_id], images[sprite_id]
            center, ground_y = 150 + i * 300, 430
            pygame.draw.rect(window, "#3e363e", (12 + i * 300, 108, 276, 478), border_radius=6)
            # A 64x32 floor diamond shows the original game's tile scale at 2x.
            diamond = [(center, ground_y - 32), (center + 64, ground_y), (center, ground_y + 32), (center - 64, ground_y)]
            pygame.draw.polygon(window, "#675747", diamond)
            pygame.draw.polygon(window, "#9f8669", diamond, 1)
            scaled = pygame.transform.scale_by(image, 2)
            window.blit(scaled, (center - entry["anchor"][0] * 2, ground_y - entry["anchor"][1] * 2))
            window.blit(label.render(direction.upper(), True, "#f3dfb6"), (center - 14, 126))
            window.blit(label.render(f"Footprint: {entry['footprint'][0]} x {entry['footprint'][1]}", True, "#ccb494"), (center - 89, 530))
        window.blit(label.render("4 drawn views / transparent PNGs / nearest-neighbor display at 2x", True, "#ccb494"), (26, 615))
        pygame.display.flip()
        frame += 1
        if limit and frame >= limit:
            running = False
        if not args.headless:
            clock.tick(30)
    if args.screenshot:
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        pygame.image.save(window, args.screenshot)
    pygame.quit()
    print(f"Preview completed: {frame} frames, {len(names)} objects loaded")


if __name__ == "__main__":
    main()
