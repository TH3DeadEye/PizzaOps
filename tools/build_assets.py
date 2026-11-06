"""Rebuild the distributable sprites from the included isolated source atlases."""

import json
import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

ROOT = Path(__file__).resolve().parents[1] / "assets" / "pizzaops"
SPRITES = {}
IMAGES = {}
ANIMATIONS = {}
INK = "#49343b"
CREAM = "#f3dfb6"
CHARACTERS = ["chef", "cashier", "stock_worker", "customer_amber", "customer_teal", "customer_lavender"]


def surface(size):
    return pygame.Surface(size, pygame.SRCALPHA)


def save(image, relative):
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(image, str(path))


def register(name, image, category, anchor=None, footprint=None, **extra):
    relative = f"sprites/{category}/{name}.png"
    save(image, relative)
    SPRITES[name] = {
        "file": relative,
        "size": list(image.get_size()),
        "anchor": anchor or [image.get_width() // 2, image.get_height() - 4],
        "category": category,
        "footprint": footprint or [1, 1],
        "bounds": list(image.get_bounding_rect()),
        **extra,
    }
    IMAGES[name] = image


def clean_cell(sheet, column, row, columns=4, rows=4):
    left = round(column * sheet.get_width() / columns)
    top = round(row * sheet.get_height() / rows)
    right = round((column + 1) * sheet.get_width() / columns)
    bottom = round((row + 1) * sheet.get_height() / rows)
    image = sheet.subsurface((left, top, right - left, bottom - top)).copy()
    mask = pygame.mask.from_surface(image, 160)
    components = mask.connected_components()
    largest = max(component.count() for component in components)
    retained = pygame.mask.Mask(image.get_size())
    for component in components:
        if component.count() > max(12, largest * 0.002):
            retained.draw(component, (0, 0))
    for pixel_y in range(image.get_height()):
        for pixel_x in range(image.get_width()):
            color = image.get_at((pixel_x, pixel_y))
            image.set_at((pixel_x, pixel_y), (*color[:3], 255) if retained.get_at((pixel_x, pixel_y)) else (0, 0, 0, 0))
    return image


def props():
    groups = {
        "kitchen": [
            ("oven_off", 80, 88), ("oven_lit_0", 80, 88), ("oven_lit_1", 80, 88), ("oven_lit_2", 80, 88),
            ("prep_counter", 64, 56), ("topping_station", 64, 60), ("storage_shelf", 53, 78), ("fridge", 48, 80),
            ("crate_tomatoes", 34, 30), ("crate_cheese", 34, 30), ("crate_flour", 34, 33), ("crate_empty", 34, 27),
            ("service_counter", 80, 54), ("register", 23, 26), ("pizza_display", 60, 50), ("sink_counter", 64, 56),
        ],
        "dining": [
            ("table_square", 60, 50), ("table_round", 54, 52), ("chair_se", 29, 43), ("chair_sw", 29, 43),
            ("floor_lamp", 25, 62), ("plant", 28, 37), ("olive_tree", 42, 67), ("pendant_lamp", 25, 52),
            ("menu_stand", 33, 46), ("awning", 84, 42), ("door_open", 48, 74), ("window", 36, 61),
            ("booth", 64, 58), ("rug", 90, 43), ("exterior_lantern", 28, 50), ("trash_bin", 28, 40),
        ],
        "items": [
            ("dough", 17, 11), ("pizza_raw", 24, 15), ("pizza_cheese", 25, 16), ("pizza_pepperoni", 25, 16),
            ("pizza_box", 26, 18), ("pizza_box_open", 28, 28), ("pizza_box_stack", 27, 27), ("pizza_slice", 18, 12),
            ("pizza_peel", 29, 27), ("sauce_pot", 22, 22), ("flour_sack", 20, 24), ("basil", 20, 25),
            ("salt_pepper", 12, 16), ("sauce_bottle", 9, 19), ("wall_clock", 22, 24), ("menu_board", 51, 38),
        ],
    }
    for group, entries in groups.items():
        sheet = pygame.image.load(str(ROOT / "source" / f"{group}.png"))
        for index, (name, width, height) in enumerate(entries):
            cell = clean_cell(sheet, index % 4, index // 4)
            cropped = cell.subsurface(cell.get_bounding_rect()).copy()
            fitted = pygame.transform.scale(cropped, (width, height))
            image = surface((96, 128))
            image.blit(fitted, (48 - width // 2, 120 - height))
            register(name, image, group, [48, 120], [2, 2] if name.startswith("oven") else [1, 1])
    animation("oven/lit", ["oven_lit_0", "oven_lit_1", "oven_lit_2", "oven_lit_1"], [180, 140, 160, 140])


def animation(name, frame_ids, durations):
    width, height = SPRITES[frame_ids[0]]["size"]
    strip = surface((width * len(frame_ids), height))
    for index, frame_id in enumerate(frame_ids):
        strip.blit(IMAGES[frame_id], (index * width, 0))
    relative = f"strips/{name}.png"
    save(strip, relative)
    ANIMATIONS[name] = {"frames": frame_ids, "durations_ms": durations, "loop": True, "strip": relative,
                        "frame_size": [width, height], "anchor": SPRITES[frame_ids[0]]["anchor"]}


def characters():
    for character in CHARACTERS:
        sheet = pygame.image.load(str(ROOT / "source" / f"{character}.png"))
        cells = [clean_cell(sheet, index % 4, index // 4, 4, 6) for index in range(24)]
        ratio = (48 if character == "chef" else 44) / cells[0].get_bounding_rect().height
        native = []
        for cell in cells:
            bounds = cell.get_bounding_rect()
            cropped = cell.subsurface(bounds).copy()
            resized = pygame.transform.scale(cropped, (round(bounds.width * ratio), round(bounds.height * ratio)))
            foot_pixels = [pixel_x for pixel_y in range(max(0, resized.get_height() - 7), resized.get_height())
                           for pixel_x in range(resized.get_width()) if resized.get_at((pixel_x, pixel_y)).a]
            foot_center = (min(foot_pixels) + max(foot_pixels)) // 2
            image = surface((48, 64))
            image.blit(resized, (24 - foot_center, 60 - resized.get_height()))
            native.append(image)
        clips = {"idle": [0, 1], "talk": [2, 3], "walk": [4, 5, 6, 7], "work": [12, 13, 14, 15],
                 "carry": [16, 17, 18, 19], "frustrated": [20, 21, 22, 23]}
        for action, indices in clips.items():
            for direction in (["se", "sw", "ne", "nw"] if action == "walk" else ["se", "sw"]):
                frame_ids = []
                for frame_index, source_index in enumerate(indices):
                    if direction in ("ne", "nw"):
                        source_index += 4
                    image = native[source_index].copy()
                    if direction in ("sw", "nw"):
                        image = pygame.transform.flip(image, True, False)
                        image.scroll(dx=1)
                        image.fill((0, 0, 0, 0), (0, 0, 1, 64))
                    name = f"{character}_{action}_{direction}_{frame_index:02d}"
                    register(name, image, "characters", [24, 60], [0.5, 0.5], feet=[24, 60],
                             character=character, action=action, direction=direction, mirrored=direction in ("sw", "nw"))
                    frame_ids.append(name)
                duration = {"idle": 500, "talk": 240, "walk": 140, "work": 220, "carry": 160, "frustrated": 320}[action]
                animation(f"{character}/{action}/{direction}", frame_ids, [duration] * len(frame_ids))


def floor_tile(name, base, trim, style):
    image = surface((64, 32))
    def point(axis_x, axis_y):
        return (32 + axis_x - axis_y, (axis_x + axis_y) // 2)
    pygame.draw.polygon(image, base, [(32, 0), (63, 16), (32, 31), (0, 16)])
    if style == "check":
        for tile_x in range(2):
            for tile_y in range(2):
                if (tile_x + tile_y) % 2:
                    corners = [point(tile_x * 16, tile_y * 16), point((tile_x + 1) * 16, tile_y * 16),
                               point((tile_x + 1) * 16, (tile_y + 1) * 16), point(tile_x * 16, (tile_y + 1) * 16)]
                    pygame.draw.polygon(image, trim, corners)
    else:
        for offset in (8, 16, 24):
            pygame.draw.line(image, trim, point(offset, 0), point(offset, 32))
    pygame.draw.lines(image, trim, False, [(0, 16), (32, 0), (63, 16)], 1)
    register(name, image, "tiles", [32, 16])


def architecture():
    floor_tile("floor_terracotta", "#c08762", "#986451", "plain")
    floor_tile("floor_cream", "#ead6af", "#d2ba92", "plain")
    floor_tile("floor_checker", "#eddbb8", "#9da489", "check")
    floor_tile("floor_wood", "#ad7c57", "#83583f", "plain")
    floor_tile("floor_pavement", "#a7aaa3", "#828e89", "plain")
    for direction in ("nw", "ne"):
        for variant in ("cream", "brick", "half"):
            image = surface((64, 96))
            height = 32 if variant == "half" else 64
            polygon = [(0, 80 - height), (32, 64 - height), (32, 64), (0, 80)]
            pygame.draw.polygon(image, "#e7cfa3" if variant != "brick" else "#bc785d", polygon)
            for level in range(8, height, 8):
                pygame.draw.line(image, "#c4aa87" if variant != "brick" else "#98624f", (0, 80 - level), (32, 64 - level))
                if variant == "brick":
                    for offset in range(8 if level % 16 else 0, 32, 16):
                        pygame.draw.line(image, "#98624f", (offset, 80 - level - offset // 2), (offset, 87 - level - offset // 2))
            pygame.draw.line(image, "#855c45", (0, 77), (32, 61), 4)
            pygame.draw.line(image, "#f4dfb6", (0, 80 - height), (32, 64 - height), 2)
            if direction == "ne":
                image = pygame.transform.flip(image, True, False)
            register(f"wall_{variant}_{direction}", image, "architecture", [32, 80])
    for name, positions in (("corner_inner", ["nw", "ne"]), ("corner_outer", ["nw", "ne"])):
        image = surface((64, 96))
        for direction in positions:
            image.blit(IMAGES[f"wall_cream_{direction}"], (0, 0))
        if name == "corner_outer":
            pygame.draw.polygon(image, "#96664a", [(29, 2), (33, 0), (35, 2), (35, 66), (31, 68), (29, 66)])
        register(name, image, "architecture", [32, 80])
    for direction in ("se", "sw"):
        image = surface((64, 40))
        pygame.draw.polygon(image, "#7c5645", [(0, 16), (32, 32), (32, 39), (0, 23)])
        pygame.draw.line(image, "#bc9064", (0, 16), (32, 32), 2)
        if direction == "se":
            image = pygame.transform.flip(image, True, False)
        register(f"foundation_{direction}", image, "architecture", [32, 16])


def ui():
    for name, size in (("panel", (144, 96)), ("tooltip", (112, 32)), ("button", (64, 24)), ("button_hover", (64, 24)),
                       ("button_pressed", (64, 24)), ("button_disabled", (64, 24)), ("speech_bubble", (64, 32)), ("bar_empty", (96, 10))):
        image = surface(size)
        pygame.draw.rect(image, INK, (0, 0, size[0], size[1] - 3), border_radius=3)
        pygame.draw.rect(image, "#d5b58a" if name == "button_pressed" else CREAM, (2, 2, size[0] - 4, size[1] - 7), border_radius=2)
        if name == "button_hover":
            pygame.draw.rect(image, "#c57c50", (3, 3, size[0] - 6, size[1] - 9), 1)
        if name == "button_disabled":
            pygame.draw.rect(image, "#b7ac99", (2, 2, size[0] - 4, size[1] - 7))
        if name == "speech_bubble":
            pygame.draw.polygon(image, INK, [(13, 27), (21, 27), (13, 31)])
        register(f"ui_{name}", image, "ui", [0, 0], [0, 0], nine_slice=[4, 4, 4, 7])
    for name, color in (("calm", "#719783"), ("busy", "#d9ae5e"), ("stressed", "#bc6257")):
        image = surface((16, 16))
        pygame.draw.circle(image, INK, (8, 8), 7)
        pygame.draw.circle(image, color, (8, 8), 6)
        pygame.draw.line(image, INK, (5, 5), (5, 7))
        pygame.draw.line(image, INK, (10, 5), (10, 7))
        pygame.draw.line(image, INK, (5, 10), (10, 10))
        if name == "calm":
            pygame.draw.line(image, INK, (6, 11), (9, 11))
        register(f"ui_mood_{name}", image, "ui", [8, 8], [0, 0])
    for name in ("play", "pause", "coin", "heart", "warning"):
        image = surface((16, 16))
        if name == "play":
            pygame.draw.polygon(image, "#719783", [(4, 2), (13, 8), (4, 14)])
        elif name == "pause":
            pygame.draw.rect(image, INK, (4, 3, 3, 10))
            pygame.draw.rect(image, INK, (9, 3, 3, 10))
        elif name == "coin":
            pygame.draw.circle(image, "#b0783f", (8, 8), 7)
            pygame.draw.circle(image, "#e7bb63", (8, 8), 5)
            pygame.draw.line(image, CREAM, (8, 5), (8, 11), 2)
        elif name == "heart":
            pygame.draw.polygon(image, "#b65e56", [(2, 3), (6, 3), (8, 6), (10, 3), (14, 3), (14, 8), (8, 14), (2, 8)])
        else:
            pygame.draw.polygon(image, "#d9ae5e", [(8, 1), (15, 14), (1, 14)])
            pygame.draw.line(image, INK, (8, 5), (8, 9), 2)
            pygame.draw.rect(image, INK, (8, 11, 2, 2))
        register(f"ui_{name}", image, "ui", [8, 8], [0, 0])


def atlases():
    for category in sorted({entry["category"] for entry in SPRITES.values()}):
        names = [name for name, entry in SPRITES.items() if entry["category"] == category]
        cell_width = max(IMAGES[name].get_width() for name in names) + 4
        cell_height = max(IMAGES[name].get_height() for name in names) + 4
        columns = 12 if category == "characters" else 8
        image = surface((columns * cell_width, ((len(names) + columns - 1) // columns) * cell_height))
        for index, name in enumerate(names):
            left, top = (index % columns) * cell_width + 2, (index // columns) * cell_height + 2
            image.blit(IMAGES[name], (left, top))
            SPRITES[name]["atlas"] = {"file": f"atlases/{category}.png", "rect": [left, top, *IMAGES[name].get_size()]}
        save(image, f"atlases/{category}.png")


def scene():
    objects = []
    def add(sprite, position, **extra):
        objects.append({"sprite": sprite, "position": position, **extra})
    for index in range(12):
        add("wall_brick_nw", [0, index], layer="back")
    for index in range(1, 12):
        add("wall_cream_ne", [index, 0], layer="back")
    add("corner_inner", [0, 0], layer="back")
    for sprite, position in [
        ("oven_lit_0", [1.4, 2]), ("prep_counter", [1.4, 4.2]), ("topping_station", [1.4, 6.1]),
        ("sink_counter", [1.4, 8.1]), ("storage_shelf", [3.5, 1]), ("fridge", [5.2, 1]),
        ("crate_tomatoes", [7.2, 1]), ("crate_cheese", [8.4, 1]), ("crate_flour", [9.6, 1]),
        ("service_counter", [4.6, 5.2]), ("pizza_display", [4.6, 7.2]),
        ("table_square", [7.5, 4.3]), ("table_round", [9.5, 7.5]),
        ("chair_sw", [8.5, 4.3]), ("chair_se", [7.5, 5.4]),
        ("chair_se", [9.5, 8.6]), ("chair_sw", [10.6, 7.5]),
        ("booth", [9, 2]), ("olive_tree", [11, 1]), ("floor_lamp", [6.2, 2]),
        ("plant", [10.8, 10.5]), ("menu_stand", [5.5, 10.7]), ("trash_bin", [0.8, 10.7]),
    ]:
        add(sprite, position, animation="oven/lit" if sprite.startswith("oven_lit") else None)
    add("register", [4.6, 5.2], elevation=34, depth_bias=0.01)
    add("pizza_pepperoni", [7.5, 4.3], elevation=33, depth_bias=0.01)
    add("rug", [6.8, 9.4], layer="ground")
    add("window", [0.2, 5.7], elevation=35, layer="back")
    add("menu_board", [6.5, 0.1], elevation=32, layer="back")
    add("wall_clock", [3, 0.1], elevation=49, layer="back")
    characters = [
        {"character": "chef", "position": [2.8, 3.7], "action": "work", "direction": "sw"},
        {"character": "cashier", "position": [3.4, 5.2], "action": "talk", "direction": "se"},
        {"character": "stock_worker", "position": [6.5, 1.7], "action": "work", "direction": "se"},
        {"character": "customer_amber", "position": [5.7, 5.3], "action": "idle", "direction": "sw"},
        {"character": "customer_teal", "position": [8.1, 7.4], "action": "talk", "direction": "se"},
        {"character": "customer_lavender", "position": [3.2, 9.4], "action": "walk", "direction": "se",
         "route": [[3.2, 9.4], [8.6, 9.4], [8.6, 10.5], [3.2, 10.5]]},
    ]
    layout = {"schema_version": 1, "size": [12, 12], "tile_size": [64, 32], "origin": [414, 152],
              "floors": [{"position": [tile_x, tile_y], "sprite": "floor_checker" if tile_x < 4 else "floor_terracotta"}
                         for tile_x in range(12) for tile_y in range(12)], "objects": objects, "characters": characters}
    (ROOT / "scene.json").write_text(json.dumps(layout, indent=2) + "\n")


def contact_sheet():
    pygame.font.init()
    font = pygame.font.Font(None, 16)
    names = [name for name, entry in SPRITES.items() if entry["category"] != "characters"]
    names.extend(f"{character}_idle_se_00" for character in CHARACTERS)
    columns = 8
    canvas = surface((columns * 130, ((len(names) + columns - 1) // columns) * 150 + 50))
    canvas.fill("#302c32")
    canvas.blit(pygame.font.Font(None, 28).render("PIZZAOPS / MODULAR ASSET LIBRARY", True, CREAM), (16, 12))
    for index, name in enumerate(names):
        left, top = (index % columns) * 130, 50 + (index // columns) * 150
        pygame.draw.rect(canvas, "#3b353b", (left + 3, top + 3, 124, 144))
        image = IMAGES[name]
        if image.get_width() > 120:
            image = pygame.transform.scale(image, (120, round(image.get_height() * 120 / image.get_width())))
        canvas.blit(image, (left + 65 - image.get_width() // 2, top + 128 - image.get_height()))
        label = name.replace("_", " ").replace("customer ", "").replace(" idle se 00", "")
        canvas.blit(font.render(label, True, CREAM), (left + 6, top + 131))
    save(canvas, "previews/contact_sheet.png")


def main():
    pygame.display.init()
    pygame.display.set_mode((1, 1))
    props()
    characters()
    architecture()
    ui()
    atlases()
    scene()
    contact_sheet()
    manifest = {"schema_version": 1, "name": "PizzaOps Cozy Isometric Pack", "version": "1.0.0", "tile_size": [64, 32],
                "projection": "screen_x=(map_x-map_y)*32; screen_y=(map_x+map_y)*16", "alpha": "binary RGBA",
                "sprites": SPRITES, "animations": ANIMATIONS,
                "limitations": ["West-facing character frames mirror east-facing frames, including lighting and handedness.",
                                "Walk has four directions; other actions have southeast and southwest only.",
                                "Furniture uses authored isometric perspective; only modular tiles have exact 2:1 raster geometry.",
                                "Demo is an asset showcase, not connected to service state."]}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Built {len(SPRITES)} sprites and {len(ANIMATIONS)} animation strips")
    pygame.quit()


if __name__ == "__main__":
    main()
