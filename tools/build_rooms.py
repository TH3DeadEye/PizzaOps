"""Assemble editable room maps from the existing sprites; never flatten the artwork."""

import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "assets" / "pizzaops"
ROOMS = {}


def room(name, title, purpose, floor, wall="cream"):
    scene = {"schema_version": 2, "id": name, "title": title, "purpose": purpose,
             "size": [8, 8], "tile_size": [64, 32], "origin": [400, 180],
             "floors": [{"sprite": floor, "position": [x, y]} for x in range(8) for y in range(8)],
             "objects": [], "characters": [], "blocked": [], "doors": [], "labels": []}
    for n in range(8):
        obj(scene, f"wall_{wall}_nw", 0, n, layer="back", boundary="west")
        obj(scene, f"wall_{wall}_ne", n, 0, layer="back", boundary="north")
    ROOMS[name] = scene
    return scene


def obj(scene, sprite, x, y, *, block=False, **fields):
    record = {"sprite": sprite, "position": [x, y], **fields}
    scene["objects"].append(record)
    if block:
        cells = [[x, y]] if block is True else block
        scene["blocked"].extend(cells)
        record["occupied"] = cells
    return record


def actor(scene, character, x, y, action="idle", direction="se", route=None):
    record = {"character": character, "position": [x, y], "action": action, "direction": direction}
    if route:
        record["route"] = route
    scene["characters"].append(record)


def tabletop(scene, sprite, x, y, height=32):
    obj(scene, sprite, x, y, elevation=height, depth_bias=0.02)


def table(scene, x, y, round_table=False, meal=None):
    obj(scene, "table_round" if round_table else "table_square", x, y, block=True)
    obj(scene, "chair_se", x, y + 1, block=True)
    obj(scene, "chair_sw", x + 1, y, block=True)
    if meal:
        tabletop(scene, meal, x, y)


def build():
    ROOMS.clear()
    s = room("kitchen", "01 / Pizza kitchen", "Wash > prepare > top > bake > hand off. A clear central aisle joins each station.", "floor_checker", "brick")
    obj(s, "oven_lit_0", 1, 1, block=[[0, 0], [1, 0], [0, 1], [1, 1]], animation="oven/lit")
    obj(s, "prep_counter", 1, 3, block=True)
    tabletop(s, "dough", 1, 3, 34)
    obj(s, "topping_station", 1, 5, block=True)
    obj(s, "sink_counter", 5, 1, block=True)
    obj(s, "trash_bin", 6, 1, block=True)
    obj(s, "prep_counter", 5, 5, block=True)
    tabletop(s, "pizza_raw", 5, 5, 34)
    obj(s, "basil", 5.35, 4.85, elevation=34, depth_bias=0.02)
    obj(s, "pizza_peel", 0.3, 1.7)
    obj(s, "sauce_pot", 0.9, 4.9, elevation=32, depth_bias=0.04)
    obj(s, "wall_clock", 3, 0, elevation=38, layer="back")
    actor(s, "chef", 2, 3, "work", "sw")
    s["labels"] = [{"text": "KITCHEN", "position": [3, 6.5]}]

    s = room("stockroom", "02 / Stock & receiving", "Dry goods stay on shelves. Chilled ingredients stay beside the fridge. Deliveries have their own staging area.", "floor_cream")
    obj(s, "storage_shelf", 1, 1, block=True)
    obj(s, "storage_shelf", 3, 1, block=True)
    obj(s, "fridge", 6, 1, block=True)
    obj(s, "crate_flour", 1, 5, block=True)
    obj(s, "crate_cheese", 3, 5, block=True)
    obj(s, "crate_tomatoes", 5, 5, block=True)
    obj(s, "crate_empty", 6, 6, block=True)
    obj(s, "flour_sack", 1, 6, block=True)
    obj(s, "pizza_box_stack", 3, 1, elevation=58, depth_bias=0.02)
    actor(s, "stock_worker", 3, 3, "work", "se")
    s["labels"] = [{"text": "STOCKROOM", "position": [4.5, 6.8]}]

    s = room("pickup", "03 / Order & pickup", "Enter, order at the register, collect at the display. The staff aisle stays behind the counter.", "floor_terracotta")
    obj(s, "service_counter", 2, 2, block=True)
    tabletop(s, "register", 2, 2, 35)
    obj(s, "service_counter", 4, 2, block=True)
    tabletop(s, "pizza_box_open", 4, 2, 35)
    obj(s, "pizza_box_stack", 4.5, 1.8, elevation=35, depth_bias=0.02)
    obj(s, "pizza_display", 6, 2, block=True)
    obj(s, "menu_board", 4, 0, elevation=28, layer="back")
    obj(s, "window", 0, 3, elevation=22, layer="back")
    obj(s, "menu_stand", 1, 6, block=True)
    obj(s, "plant", 6, 6, block=True)
    obj(s, "rug", 3, 6, layer="ground")
    actor(s, "cashier", 2, 1, "talk", "sw")
    actor(s, "customer_amber", 2, 3, "talk", "se")
    actor(s, "customer_lavender", 3, 5, "walk", "se", [[3, 5], [4, 5], [4, 6], [3, 6]])
    s["labels"] = [{"text": "ORDER + PICKUP", "position": [5, 7]}]

    s = room("dining", "04 / Dining room", "Two table settings and a booth nook. Guests can reach every seat without crossing the kitchen.", "floor_wood")
    obj(s, "booth", 5, 1, block=True)
    obj(s, "window", 0, 2, elevation=25, layer="back")
    obj(s, "window", 0, 5, elevation=25, layer="back")
    obj(s, "floor_lamp", 6, 1, block=True)
    obj(s, "olive_tree", 1, 1, block=True)
    table(s, 2, 3, meal="pizza_cheese")
    obj(s, "salt_pepper", 2.35, 2.85, elevation=32, depth_bias=0.02)
    table(s, 5, 5, round_table=True)
    obj(s, "plant", 1, 6, block=True)
    actor(s, "customer_teal", 4, 1, "talk", "se")
    s["labels"] = [{"text": "DINING ROOM", "position": [4, 7]}]

    s = room("patio", "05 / Courtyard patio", "A separate outdoor room: shaded entrance, cafe tables, plants and a clear path through the middle.", "floor_pavement", "brick")
    # Only the building side is enclosed; the courtyard's other edge is open.
    s["objects"] = [o for o in s["objects"] if o.get("boundary") != "north"]
    obj(s, "door_open", 0, 3, layer="back")
    obj(s, "awning", 0, 3, elevation=65, layer="back")
    obj(s, "exterior_lantern", 0, 5, elevation=42, layer="back")
    table(s, 3, 2, round_table=True)
    obj(s, "pizza_slice", 3.25, 2.2, elevation=32, depth_bias=0.02)
    table(s, 5, 5, round_table=True)
    obj(s, "olive_tree", 6, 1, block=True)
    obj(s, "plant", 1, 6, block=True)
    obj(s, "menu_stand", 2, 5, block=True)
    obj(s, "trash_bin", 6, 7, block=True)
    actor(s, "customer_amber", 2, 2, "talk", "se")

    s = room("lounge", "06 / Staff break room", "A quiet room away from food preparation, with a shared table, sofa corner and warm lighting.", "floor_wood")
    obj(s, "booth", 1, 2, block=True)
    obj(s, "rug", 2, 3, layer="ground")
    obj(s, "floor_lamp", 1, 1, block=True)
    obj(s, "window", 0, 4, elevation=25, layer="back")
    table(s, 5, 4, round_table=True)
    obj(s, "pizza_box", 5.25, 4.1, elevation=32, depth_bias=0.02)
    obj(s, "plant", 6, 1, block=True)
    obj(s, "storage_shelf", 4, 1, block=True)
    obj(s, "wall_clock", 2, 0, elevation=38, layer="back")
    actor(s, "stock_worker", 2, 2, "idle", "sw")
    actor(s, "chef", 4, 4, "talk", "se")

    full = {"schema_version": 2, "id": "restaurant", "title": "Little Slice / Restaurant",
            "purpose": "Four connected rooms. Kitchen and stock at the back; ordering and dining at the front.",
            "size": [16, 16], "tile_size": [64, 32], "origin": [520, 200],
            "floors": [], "objects": [], "characters": [], "blocked": [], "doors": [], "labels": [], "rooms": []}
    for name, offset in (("kitchen", (0, 0)), ("stockroom", (8, 0)), ("pickup", (0, 8)), ("dining", (8, 8))):
        module = deepcopy(ROOMS[name])
        dx, dy = offset
        full["rooms"].append({"id": name, "title": module["title"], "bounds": [dx, dy, 8, 8]})
        for category in ("floors", "objects", "characters", "labels"):
            for record in module[category]:
                boundary = record.get("boundary")
                internal = (boundary == "west" and dx) or (boundary == "north" and dy)
                if internal:
                    axis = 1 if boundary == "west" else 0
                    if record["position"][axis] in (3, 4):
                        full["doors"].append([record["position"][0] + dx, record["position"][1] + dy])
                        continue
                    record["sprite"] = "wall_half_nw" if boundary == "west" else "wall_half_ne"
                    record["layer"] = "solid"
                    # Occupy both sides of the dividing wall. Doorways are two tiles wide.
                    x, y = record["position"][0] + dx, record["position"][1] + dy
                    full["blocked"].extend([[x, y], [x - 1, y] if boundary == "west" else [x, y - 1]])
                if name == "dining" and record.get("layer") == "back" and not boundary:
                    continue  # tall windows do not float above cutaway partitions
                record["position"] = [record["position"][0] + dx, record["position"][1] + dy]
                record["room"] = name
                if "occupied" in record:
                    record["occupied"] = [[x + dx, y + dy] for x, y in record["occupied"]]
                if "route" in record:
                    record["route"] = [[x + dx, y + dy] for x, y in record["route"]]
                full[category].append(record)
        full["blocked"].extend([[x + dx, y + dy] for x, y in module["blocked"]])
    # Front edges stay cut away so the room interiors remain visible.
    for n in range(16):
        obj(full, "foundation_se", 15, n, layer="ground")
        obj(full, "foundation_sw", n, 15, layer="ground")
    full["landmarks"] = {
        "entrance": [3, 15], "wait_slots": [[2, 11], [3, 11], [3, 12], [4, 12], [5, 12], [5, 13]],
        "overflow": [4, 14], "table_seats": [[12, 9], [9, 13], [12, 14]],
        "homes": {"chef": [[2, 3], "sw"], "cashier": [[2, 9], "sw"], "stock_worker": [[11, 3], "se"]},
        "oven": [2, 2], "pass": [4, 9], "crates": [11, 4]}
    full["blocked"] = [list(p) for p in sorted(set(map(tuple, full["blocked"])))]
    return full


def main():
    full = build()
    (ROOT / "rooms").mkdir(exist_ok=True)
    for name, scene in ROOMS.items():
        (ROOT / "rooms" / f"{name}.json").write_text(json.dumps(scene, indent=2) + "\n")
    (ROOT / "scene.json").write_text(json.dumps(full, indent=2) + "\n")
    print(f"Built {len(ROOMS)} room modules and the connected restaurant")


if __name__ == "__main__":
    main()
