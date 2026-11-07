# PizzaOps — Cozy Isometric Asset Pack

Original modular restaurant artwork, individual transparent PNGs, animation strips, metadata, and an offline Pygame showcase. No backend services are required to inspect the pack.

## Run in PizzaOps

From the project folder:

```sh
uv run python -m viewer.main
```

Or use the existing environment directly:

```sh
.venv/bin/python -m viewer.main
```

The viewer currently demonstrates artwork and movement only; it does not read service events or claim that any orders have completed.

## Run the services

Order (8080), Kitchen (8081) and Inventory (8082) are three separate processes that talk only over HTTP; see `ARCHITECTURE.md` for the plan and `CONTRACTS.md` for the API. All services of one simulation must share a run ID, and every business request carries it as `X-Run-Id`. One terminal per service:

```sh
export PIZZAOPS_RUN_ID=$(uuidgen | tr A-Z a-z)                                # same value in every terminal
uv run uvicorn services.inventory.main:app --host 127.0.0.1 --port 8082
KITCHEN_INVENTORY_URL=http://127.0.0.1:8082 uv run uvicorn services.kitchen.main:app --host 127.0.0.1 --port 8081
ORDER_KITCHEN_URL=http://127.0.0.1:8081 uv run uvicorn services.order.main:app --host 127.0.0.1 --port 8080
```

Place an order and watch it travel:

```sh
H=(-H "X-Run-Id: $PIZZAOPS_RUN_ID" -H "Content-Type: application/json")
REQ=$(uuidgen | tr A-Z a-z); WHO=$(uuidgen | tr A-Z a-z)
ORDER=$(curl -s "${H[@]}" -d "{\"requestId\":\"$REQ\",\"customer\":{\"id\":\"$WHO\",\"appearance\":\"customer_amber\",\"trait\":\"patient\"},\"pizzaType\":\"PEPPERONI\",\"quantity\":2}" http://127.0.0.1:8080/orders | jq -r .orderId)
curl -s "${H[@]}" http://127.0.0.1:8080/orders/$ORDER | jq '{status, failureReason, kitchenStatus, reservationId, traceId}'
curl -s "${H[@]}" http://127.0.0.1:8081/cook/$ORDER | jq '{status, items}'
curl -s "${H[@]}" http://127.0.0.1:8082/stock | jq .items
curl -s "${H[@]}" http://127.0.0.1:8081/mood
```

The order is `RECEIVED` at once and `READY` a few seconds later (Kitchen cooks for `KITCHEN_COOKING_S`, default 3 s). Repeating the same `POST` returns the same order with `200`; stopping Kitchen makes the next order `FAILED` with `KITCHEN_UNAVAILABLE` instead of hanging. Each service prints one JSON line per event; `grep <traceId>` across the three outputs follows one order through all of them. Settings are environment variables (`ORDER_KITCHEN_URL`, `ORDER_MENU`, `ORDER_WORKERS`, `KITCHEN_INVENTORY_URL`, `KITCHEN_WORKERS`, `KITCHEN_COOKING_S`, `KITCHEN_ATTEMPT_TIMEOUT_S`, `INVENTORY_MAX_QUANTITY`, ...); defaults live in each service's `settings.py`. Run the tests with `uv run pytest`; `tests/test_p0_acceptance.py` starts all three services as real processes.

## Run the standalone ZIP

Unzip `PizzaOps_Cozy_Isometric_Asset_Pack_v1.zip`, open a terminal in its folder, and run:

```sh
python -m pip install -r requirements.txt
python -m viewer.asset_demo
```

Python 3.10+ is sufficient for this standalone pack. The existing PizzaOps project requires Python 3.13+. Only `pygame-ce` is needed at runtime; Pillow and other image-processing packages are not required. Do not install both `pygame` and `pygame-ce` in the same environment.

Controls: **Space** pauses, **Tab** opens the animation studio, **G** toggles map coordinates, **+ / -** change integer zoom, and **Esc** exits. In the animation studio, **Left / Right** select an action and **Up / Down** select direction. The initial window fits the desktop where possible. `--scale 1` gives a 900 × 600 window.

## What is included

| Category | Individual PNGs | Contents |
| --- | ---: | --- |
| Kitchen | 16 | Oven off + three lit frames, prep counter, topping station, shelves, fridge, four crates, service counter, register, pizza display, sink |
| Dining / exterior | 16 | Two tables, two chairs, floor and pendant lamps, plants, olive tree, menu stand, awning, open door, window, booth, rug, lantern, trash bin |
| Items / decor | 16 | Dough, raw / cheese / pepperoni pizzas, boxes, pizza slice, peel, sauce pot, flour sack, basil, condiments, clock, menu board |
| Floor tiles | 5 | Terracotta, cream, sage checker, wood, pavement |
| Architecture | 10 | Cream / brick / half walls in two orientations, inner / outer corners, two foundation edges |
| UI | 16 | Panels, tooltip, speech bubble, four button states, empty bar, three moods, play, pause, coin, heart, warning |
| Character frames | 288 | Chef, cashier, stock worker, and three customers |
| **Total** | **367** | All are independently loadable RGBA PNGs |

Also included: **85 horizontal animation strips**, **7 category atlases**, the nine original generation sheets, an example restaurant layout, three preview images, an animated GIF showing four actions, the loader, demo, validation tools, and tests.

## File guide

```text
assets/pizzaops/
  sprites/            Every individual runtime PNG
  strips/             Character/action/direction.png; oven/lit.png
  atlases/            Convenience category sheets with 2px gutters
  manifest.json       Sprite dimensions, categories, anchors, bounds, atlas rectangles
  scene.json          Editable 12 × 12 example floor plan and object positions
  validation.json     Machine-readable validation results
  previews/           Restaurant, character gallery, and contact sheet
  source/             Original isolated generation sheets and provenance
viewer/assets.py      AssetPack and Animator
viewer/asset_demo.py  Standalone scene and animation studio
tools/build_assets.py Rebuild all runtime PNGs and metadata
tools/validate_assets.py Check alpha, frames, sheets, and scene references
```

Source sheets are archival art, not runtime atlases. They contain soft alpha and have no registration guarantees. Use `sprites/`, `strips/`, or `atlases/` in your game. The supplied runtime PNGs use only alpha 0 or 255, preventing background haze and blurry edges. All fully transparent runtime pixels have zero RGB.

## Animation coverage

Each character has 48 individual exported frames and 14 clips:

| Action | Frames per direction | Directions | Timing |
| --- | ---: | --- | --- |
| idle | 2 | SE, SW | 500 ms |
| talk | 2 | SE, SW | 240 ms |
| walk | 4 | SE, SW, NE, NW | 140 ms |
| work | 4 | SE, SW | 220 ms |
| carry | 4 | SE, SW | 160 ms |
| frustrated | 4 | SE, SW | 320 ms |

All character frames are **48 × 64**, with the feet point **(24, 60)**. The chef's hat makes him slightly taller than the other characters. The chef kneads dough, the cashier writes orders, the stock worker checks inventory, and customers use a phone or read a menu. Carrying includes pizza, boxes, or a produce crate.

The lit oven clip has four timeline entries using three unique flame images. `oven_off` is a separate static sprite.

## Loading an image and animation

Create a Pygame display before `convert_alpha()` is used:

```python
import pygame
from viewer.assets import AssetPack, Animator

pygame.init()
screen = pygame.display.set_mode((900, 600))
pack = AssetPack()
chef = Animator(pack, "chef/walk/se")
clock = pygame.time.Clock()
running = True
while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
    chef.update(clock.tick(60))
    screen.fill("#302c32")
    pack.draw(screen, "prep_counter", (350, 260), scale=2)
    pack.draw(screen, chef.frame_id, (480, 290), scale=2)
    pygame.display.flip()
pygame.quit()
```

`AssetPack(use_atlas=True)` uses the same public interface but reads atlas rectangles. Every atlas region and strip frame is checked against its individual PNG byte-for-byte. To bypass the loader, call `pygame.image.load(path).convert_alpha()`. Strip frame rectangles are `(index * width, 0, width, height)`.

Pixel-preserving loading and scaling follow the official [Pygame image documentation](https://pyga.me/docs/ref/image.html) and [transform documentation](https://pyga.me/docs/ref/transform.html). Always use integer scale factors and `pygame.transform.scale`, not `smoothscale`.

## Geometry, anchors, and sorting

The floor grid is exactly **64 × 32**, a 2:1 diamond. Tile positions in `scene.json` specify tile centers:

```python
screen_x = origin_x + (map_x - map_y) * 32
screen_y = origin_y + (map_x + map_y) * 16 - elevation
```

Draw each image at `(screen_x - anchor_x, screen_y - anchor_y)`. Character feet and furniture anchors represent the ground contact point; UI anchors are local coordinates. Items placed on counters use `elevation` without changing their ground position. Most furniture uses padded 96 × 128 canvases; do not assume the visible sprite fills its canvas. `bounds` provides the occupied rectangle.

Floors draw first, then `ground` decor and `back` walls, then characters and objects sorted by `(map_x + map_y + depth_bias, map_x)`. A countertop item uses the same map position as its counter, positive elevation, and a small positive depth bias. `footprint` is a suggested occupied grid extent, not a collision polygon. Complex intersecting multi-tile furniture may require your own occlusion or collision handling.

Walls sit on rear tile edges: `wall_*_nw` along the map-x=0 boundary, `wall_*_ne` along map-y=0. The matching corner closes their intersection. Foundation sprites can be placed on the two visible front edges. All have explicit anchors; no automatic cropping is needed.

To adapt the example, edit `floors`, `objects`, and `characters` in `scene.json`. `position` allows fractional tile coordinates. A `route` is a demonstration waypoint loop, not pathfinding. In the eventual observer, map real service events to actions and destinations; movement should remain presentation-only.

## Verification and rebuild

```sh
python tools/validate_assets.py
python -m viewer.asset_demo --headless --frames 180 --screenshot preview.png
python -m viewer.asset_demo --headless --frames 60 --gallery --atlas
python -m pytest tests/test_assets.py -q
```

Tests additionally require `pytest`. Rebuilding uses only Pygame and the included sources:

```sh
python tools/build_assets.py
python tools/validate_assets.py
```

The builder replaces the generated files with a deterministic rebuild; preserve hand-edited sprite changes elsewhere first. Preview screenshots are refreshed by running the demo with `--screenshot`.

Run `python tools/package_assets.py` to rebuild the standalone ZIP and SHA-256 checksum in `dist/`. Generated distribution files are excluded from project version history; the sprites, sources, and tooling are committed.

## Scope and limitations

- This is a complete starting pack for the scene components listed above, not every possible restaurant asset or character action.
- The earlier conversation's two image files were not available. This set follows its cozy isometric pizzeria description rather than an exact visual match.
- West-facing poses are mirrored from east-facing artwork, including lighting and handedness. Rear views exist for walking; other actions have two front-facing directions. No seated, eating, running, or eight-direction cycles are included.
- Generated furniture uses an authored isometric perspective. The floor/wall geometry is mathematically 2:1; individual organic silhouettes and details are not CAD-perfect. Small hand-drawn variation remains between animation keyframes.
- Work and carry props are part of their character frames. The same item types are also available separately for scene assembly; arms and clothes are not separate rig layers.
- The demo uses offline movement, has no service integration, and does not implement collisions or pathfinding. Existing service scaffolds remain available for your own implementation.

Artwork credits, usage permission, and source prompts are in `ASSET_LICENSE.md` and `assets/pizzaops/source/PROVENANCE.md`. The added loader, demo, builder, and tests have the permissive license in `CODE_LICENSE.md`; that license does not change the licensing of pre-existing project files.
