# Build your own PizzaOps room

Start with **catalog.html** in the standalone kit. It opens in a browser without a server and lets you search sprites by name and category. Every card gives the exact PNG path, size and anchor. The labeled PNG catalog works without a browser. The source art was created with the built-in image-generation tool; prompts and provenance are included under `assets/pizzaops/source/`.

## What you receive

- 370 separate transparent PNGs: 82 environment, food, architecture and UI sprites plus 288 character animation frames.
- Floors: terracotta, cream, sage checker, wood and pavement; each tile is 64 × 32 pixels.
- Walls: brick, cream and half-height partitions in two orientations, corners and foundation edges.
- Dining: tables, chairs, booth, bench, bar stool and communal table.
- Kitchen and service: oven with lit/off states, prep counter, topping station, sink, register, service counter and pizza display.
- Storage: shelves, fridge, ingredient crates and flour sacks.
- Decor and exterior: lamps, plants, olive tree, rug, menu board, clock, door, window, awning and lantern.
- Food and small props: dough, pizzas, slice, boxes, peel, sauce pot, condiments and basil.
- Six characters with idle, walk, work, carry, talk and frustrated clips; four walking directions, two directions for the other actions.
- 85 animation strips, 8 atlases, metadata JSON, an asset-list CSV and six optional room layouts.

## Find the individual files

`assets/pizzaops/sprites/` contains the ready-to-import PNGs, grouped by category. The new furniture is in `sprites/environment/`. Use these PNGs for your room; the labeled catalog images are just guides. `manifest.json` contains every sprite's path, size, anchor, visible bounds and atlas rectangle, plus frame durations for animations.

The backgrounds are real transparency. Runtime pixels use alpha 0 or 255. The art sources are preserved separately and may contain soft alpha; they are not the runtime sprites.

## Assemble a room

1. Lay out floor tiles on the isometric grid.
2. Place rear walls, then doors and windows. Leave openings where people should walk.
3. Place rugs and ground decor.
4. Place furniture using its ground anchor. Keep the same scale for every object.
5. Put small objects on furniture using the same ground position plus an `elevation`.
6. Draw props and people in ground-depth order so nearby objects cover distant ones.

For tile coordinates `(x, y)`:

```python
screen_x = origin_x + (x - y) * 32
screen_y = origin_y + (x + y) * 16 - elevation
```

Then draw the image at `(screen_x - anchor_x, screen_y - anchor_y)`. The supplied `AssetPack.draw()` handles anchors for you. Use integer nearest-neighbor scaling; do not trim the transparent padding unless you update the anchor. Most original props have 96 × 128 canvases; the new furniture has 128 × 160 canvases; the visible art is scaled consistently. All character frames are 48 × 64 with feet at (24, 60).

Suggested footprints in the manifest are placement guides. For actual collision, author the occupied tiles for your arrangement. Only the floor/wall tiles have exact mathematical 2:1 geometry; furniture has the pack's authored isometric perspective. West character poses are mirrored. Seated/eating animations, bathrooms, and every possible appliance are not included.

## Try your own layout

Install the one runtime dependency from the kit folder:

```sh
python -m pip install -r requirements.txt
python -m examples.furnish_room
```

Edit `examples/my_room.json`, then rerun the preview. Each furniture object looks like:

```json
{"sprite": "communal_table", "position": [3, 3]}
```

An item on a table uses elevation and a small depth offset:

```json
{"sprite": "pizza_pepperoni", "position": [3, 3], "elevation": 36, "depth_bias": 0.02}
```

Start from `examples/empty_room.json` for an unfurnished floor and two walls. Other examples are in `assets/pizzaops/rooms/`. The interactive asset showcase is available with `python -m viewer.asset_demo`; use 1–7 to select a room and Tab to view character animations.

## Rebuild and licensing

In the full PizzaOps project, rebuild the original pack first, then its furniture extension:

```sh
python tools/build_assets.py
python tools/build_environment.py
python tools/validate_assets.py
python -m tools.package_room_kit
```

The existing ASSET_LICENSE.md covers the artwork, including the new furniture. Attribution is optional. CODE_LICENSE.md covers the supplied loader and examples. No downloaded third-party art or fonts are bundled.
