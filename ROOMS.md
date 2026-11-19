# PizzaOps room collection

Six 8 × 8 room modules reuse the original transparent sprites. The default 16 × 16 restaurant joins four modules. The artwork, anchors, animation strips and atlas coordinates stay compatible with the existing pack.

| Key | View | Purpose |
| --- | --- | --- |
| 1 | Restaurant | Connected kitchen, stockroom, pickup and dining |
| 2 | Kitchen | Oven, prep surfaces, toppings, washing and handoff |
| 3 | Stockroom | Shelves, fridge, ingredient crates and delivery staging |
| 4 | Pickup | Register, order counter, boxed orders, display and queue |
| 5 | Dining | Two table settings, booth nook, plants and lighting |
| 6 | Patio | Outdoor cafe tables, entrance canopy and greenery |
| 7 | Lounge | Staff break table and sofa corner |

The kitchen uses checker tiles, stock uses cream tiles, pickup uses terracotta, dining uses wood, and the patio uses pavement. Full-height rear walls frame the rooms. Half-height internal partitions let the viewer see people and workstations. The front walls are deliberately cut away. Two-tile doorways join adjacent rooms.

## Preview

```sh
uv run python -m viewer --mode demo
uv run python -m viewer --mode demo --room stockroom
uv run python -m viewer --mode demo --room dining --atlas
```

Press 1–7 to switch rooms, Tab for character animations, G for the walking grid, and Space to pause the preview. Green dots mark walkable tiles; red dots mark solid tiles. The sidebar occupies its own area so it does not cover the room. All sprite scaling uses nearest-neighbor integer factors.

Export actual viewer screenshots, without starting any backend services:

```sh
uv run python -m tools.render_rooms
uv run python -m viewer --mode demo --room kitchen --headless --frames 24 --scale 1 --screenshot kitchen.png
```

Screenshots are saved in `assets/pizzaops/previews/rooms/` by the first command.

## Layout files

`assets/pizzaops/rooms/<name>.json` is a self-contained local room. `assets/pizzaops/scene.json` is the connected restaurant used by demo, live and replay modes. Its room offsets are:

| Room | Map offset | Local size |
| --- | --- | --- |
| Kitchen | (0, 0) | 8 × 8 |
| Stockroom | (8, 0) | 8 × 8 |
| Pickup | (0, 8) | 8 × 8 |
| Dining | (8, 8) | 8 × 8 |

Each record references a manifest sprite ID. `position` is its ground point; `elevation` places a countertop item without changing its ground depth. Layers are `ground`, `back`, or the normal depth-sorted prop layer. Internal walls share the prop depth order with actors.

`blocked` lists occupied tile centers. `doors` documents partition openings. `landmarks` defines the entrance, queue slots, overflow point, dining waiting spots, staff homes, oven, handoff and crates. Service events use these destinations rather than old fixed coordinates. The oven lights when a live cooking job has status `COOKING`; the offline preview animates it continuously.

Actors take four-neighbor routes across walkable tiles, passing through the door openings. They may be interrupted by new events and take a fresh route. This is static navigation: actors do not avoid one another, there are no door-opening animations, and extra customers share the overflow point. Dining waiting spots use standing/talking sprites because this pack has no seated animation. Patio and lounge are visual modules only.

For consistent changes to both the standalone rooms and their restaurant composition, edit `tools/build_rooms.py`, then run:

```sh
uv run python tools/build_rooms.py
uv run python tools/validate_assets.py
uv run python -m pytest tests/viewer tests/test_assets.py -q
uv run python -m tools.render_rooms
```

Rebuilding replaces the generated JSON files. Direct JSON edits work immediately, but should be transferred to the builder before rebuilding. Rebuilding the full sprite pack also calls the room builder so the old single-room layout does not return.
