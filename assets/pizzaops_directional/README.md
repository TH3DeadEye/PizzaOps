# PizzaOps directional room furniture

Original isolated furniture for the kitchen, counter, dining room, stockroom, patio and staff room. Uses the existing game's 64 × 32 isometric grid and warm walnut, cream, terracotta and sage palette.

## Files

- `sprites/<category>/<object>_<direction>.png`: individual transparent runtime PNGs.
- `turnarounds/<object>.png`: four equal-size cells in SE, SW, NE, NW order. These cells are rotations, not animation frames.
- `atlases/<category>.png`: convenience sheets with exact rectangles in `manifest.json`.
- `previews/`: labeled contact sheets on a colored background for inspection.
- `source/`: original isolated turnaround artwork, preserved at generated resolution.
- `prompts/`: exact prompts used with the built-in image generator.
- `catalog.json`: export sizes, source cell direction assignments and suggested footprints.
- `manifest.json`: runtime image paths, sizes, anchors, bounds, directions, atlas regions and object-to-variant lookup.
- `validation.json`: final export checks.

## Preview

From the project root:

```sh
.venv/bin/python assets/pizzaops_directional/preview.py
```

Use Left/Right to browse. All four views appear together. A floor diamond shows the original 64 × 32 tile scale, enlarged with nearest-neighbor sampling.

## Use with the existing Pygame loader

```python
from pathlib import Path
from viewer.assets import AssetPack

pack = AssetPack(Path("assets/pizzaops_directional"))
direction = "se"
sprite_id = pack.manifest["objects"]["prep_bench"]["variants"][direction]
pack.draw(screen, sprite_id, (screen_x, screen_y), scale=2)
```

Initialize a Pygame display before loading images. Pass `use_atlas=True` to `AssetPack` to use the category atlases. Integer scaling preserves the pixels.

## Placement

Every runtime PNG is 160 × 192 pixels with the same anchor `[80, 144]`, representing the projected center of its ground contact. The canvas is padding; the visible artwork retains the game's small furniture scale. Use the manifest anchor rather than the image's lower edge. The frontmost base corner extends below the anchor; `ground_front_offset_px` records that export convention.

Map projection remains `screen_x=(map_x-map_y)*32`, `screen_y=(map_x+map_y)*16`. Direction describes the object's functional front: SE is map +X, SW is +Y, NW is -X, NE is -Y. Choose the supplied view; do not rotate the PNG in screen space. Source cell assignments are checked visually and may differ from generation prompt order.

Footprints and approach offsets are suggested placement data. They still need to be connected to station reservations and navigation when implementing the redesigned rooms. Fractional footprints describe small props; a tile-based collision system can reserve their containing cell. Countertop appliances need a surface elevation; hoods, awnings and the service hatch need a wall elevation. Treat the umbrella canopy and awning as overhangs rather than ground blockers. Art is independently drawn across views, so small trim and surface-detail variations remain.

This pack adds static environment artwork. Existing characters, items, floors, walls and oven animations remain in `assets/pizzaops`. The new lit oven is a static four-direction prop. The pack does not implement the room simulation or furniture behavior.

## Rebuild and validate

```sh
.venv/bin/python tools/build_directional_assets.py
.venv/bin/python tools/build_directional_assets.py --validate-only
```

The exporter isolates already-separated source cells, reduces their size with nearest-neighbor sampling and normalizes alpha to the binary convention of the original pack. It does not crop room screenshots or paint missing rear views. Original RGBA artwork remains in `source/`.

## Credits and use

Original artwork generated for PizzaOps with the built-in image generator and the included prompts. You may use and modify this pack in PizzaOps and your own projects without an attribution requirement from this package. No third-party artwork or game screenshots are bundled. No exclusive rights or third-party trademark rights are claimed.
