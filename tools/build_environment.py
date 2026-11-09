"""Export new isolated furniture sources into the existing PNG/atlas manifest.

Run after tools/build_assets.py when rebuilding the complete sprite library.
No existing sprites or animation mappings are changed.
"""

import json
import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

ROOT = Path(__file__).resolve().parents[1] / "assets" / "pizzaops"
SPECS = {
    "bench": {"fit": [76, 54], "footprint": [2, 1], "description": "Walnut bench with burgundy upholstery and iron arms"},
    "stool": {"fit": [30, 42], "footprint": [1, 1], "description": "Walnut bar stool with burgundy seat and brass footrest"},
    "communal_table": {"fit": [88, 56], "footprint": [2, 2], "description": "Long walnut dining table with cream patterned runner"},
}
CANVAS = (128, 160)
ANCHOR = (64, 152)


def export(root=ROOT):
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    (root / "sprites" / "environment").mkdir(parents=True, exist_ok=True)
    columns, cell_w, cell_h = 4, CANVAS[0] + 4, CANVAS[1] + 4
    atlas = pygame.Surface((columns * cell_w, cell_h), pygame.SRCALPHA)
    for index, (name, spec) in enumerate(SPECS.items()):
        source = pygame.image.load(root / "source" / "environment" / f"{name}.png")
        assert source.get_masks()[3], f"Source is missing alpha: {name}"
        bounds = source.get_bounding_rect(min_alpha=160)
        scale = min(spec["fit"][0] / bounds.width, spec["fit"][1] / bounds.height)
        size = (max(1, round(bounds.width * scale)), max(1, round(bounds.height * scale)))
        # Mechanical export: retain the original silhouette and aspect ratio,
        # sample at the game's pixel size, and give each sprite a stable anchor.
        fitted = pygame.transform.scale(source.subsurface(bounds), size)
        for y in range(size[1]):
            for x in range(size[0]):
                color = fitted.get_at((x, y))
                fitted.set_at((x, y), (*color[:3], 255) if color.a >= 160 else (0, 0, 0, 0))
        sprite = pygame.Surface(CANVAS, pygame.SRCALPHA)
        sprite.blit(fitted, (ANCHOR[0] - size[0] // 2, ANCHOR[1] - size[1]))
        filename = f"sprites/environment/{name}.png"
        pygame.image.save(sprite, root / filename)
        left, top = index * cell_w + 2, 2
        atlas.blit(sprite, (left, top))
        manifest["sprites"][name] = {
            "file": filename, "size": list(CANVAS), "anchor": list(ANCHOR),
            "category": "environment", "footprint": spec["footprint"],
            "bounds": list(sprite.get_bounding_rect()), "description": spec["description"],
            "atlas": {"file": "atlases/environment.png", "rect": [left, top, *CANVAS]},
        }
    pygame.image.save(atlas, root / "atlases" / "environment.png")
    manifest["version"] = "1.1.0"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Exported {len(SPECS)} furniture sprites; library now has {len(manifest['sprites'])} individual PNGs")


if __name__ == "__main__":
    export()
