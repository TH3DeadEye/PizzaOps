"""Export isolated four-view source art into aligned, independently loadable PNGs."""

import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

ROOT = Path(__file__).resolve().parents[1] / "assets" / "pizzaops_directional"
NORMALS = {"se": [1, 0], "sw": [0, 1], "nw": [-1, 0], "ne": [0, -1]}


def save(image, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(image, path)


def build(root=ROOT, allow_missing=False):
    pygame.display.init()
    pygame.display.set_mode((1, 1))
    pygame.font.init()
    catalog = json.loads((root / "catalog.json").read_text())
    size, anchor = catalog["canvas"], catalog["anchor"]
    sprites, objects, images, missing = {}, {}, {}, []
    for spec in catalog["assets"]:
        path = root / "source" / (spec["id"] + ".png")
        if not path.exists():
            missing.append(spec["id"])
            continue
        source = pygame.image.load(path).convert_alpha()
        source_alpha = pygame.image.tobytes(source, "RGBA")[3::4]
        assert min(source_alpha) == 0 and max(source_alpha) > 160, path
        cells = []
        for index in range(4):
            x, y = index % 2, index // 2
            left, right = round(x * source.width / 2), round((x + 1) * source.width / 2)
            top, bottom = round(y * source.height / 2), round((y + 1) * source.height / 2)
            cell = source.subsurface((left, top, right - left, bottom - top)).copy()
            mask = pygame.mask.from_surface(cell, 160)
            rects = mask.get_bounding_rects()
            assert rects, (spec["id"], index, "empty source cell")
            bounds = rects[0].unionall(rects[1:])
            assert bounds.left > 1 and bounds.top > 1 and bounds.right < cell.width - 1 and bounds.bottom < cell.height - 1, (spec["id"], index, "clipped source")
            cells.append(cell.subsurface(bounds).copy())
        ratio = min(spec["width"] / max(c.width for c in cells), spec["max_height"] / max(c.height for c in cells))
        variants = {}
        for direction, cell in zip(spec.get("source_order", catalog["directions"]), cells, strict=True):
            fitted = pygame.transform.scale(cell, (max(1, round(cell.width * ratio)), max(1, round(cell.height * ratio))))
            # Runtime export uses the same binary-alpha convention as the original pack.
            # This only normalizes file format; no missing artwork is painted or mirrored.
            pixels = bytearray(pygame.image.tobytes(fitted, "RGBA"))
            for offset in range(0, len(pixels), 4):
                if pixels[offset + 3] > 160:
                    pixels[offset + 3] = 255
                else:
                    pixels[offset:offset + 4] = b"\0\0\0\0"
            fitted = pygame.image.frombytes(bytes(pixels), fitted.size, "RGBA")
            ground_width = min(spec.get("ground_width", fitted.width), fitted.width)
            front_offset = round(ground_width / 4)
            image = pygame.Surface(size, pygame.SRCALPHA)
            image.blit(fitted, (anchor[0] - fitted.width // 2, anchor[1] + front_offset - fitted.height))
            name = f"{spec['id']}_{direction}"
            relative = f"sprites/{spec['category']}/{name}.png"
            save(image, root / relative)
            footprint = list(spec["footprint"])
            if direction in ("sw", "ne"):
                footprint.reverse()
            normal = NORMALS[direction]
            approach = [normal[i] * (footprint[i] + 1) / 2 for i in range(2)]
            sprites[name] = {"file": relative, "size": size, "anchor": anchor, "category": spec["category"],
                             "bounds": list(image.get_bounding_rect()), "footprint": footprint,
                             "object": spec["id"], "direction": direction, "front_normal": normal,
                             "suggested_approach_offset": approach, "source_cell": catalog["directions"].index(direction) if "source_order" not in spec else spec["source_order"].index(direction),
                             "mirrored": False, "anchor_kind": "projected ground center", "ground_front_offset_px": front_offset}
            images[name] = image
            variants[direction] = name
        strip = pygame.Surface((size[0] * 4, size[1]), pygame.SRCALPHA)
        for index, direction in enumerate(catalog["directions"]):
            strip.blit(images[variants[direction]], (index * size[0], 0))
        strip_path = f"turnarounds/{spec['id']}.png"
        save(strip, root / strip_path)
        objects[spec["id"]] = {"category": spec["category"], "description": spec["description"], "variants": variants,
                                "turnaround": strip_path, "direction_order": catalog["directions"], "frame_size": size,
                                "anchor": anchor, "source": f"source/{spec['id']}.png", "prompt": f"prompts/{spec['id']}.txt",
                                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if missing and not allow_missing:
        raise ValueError(f"Missing source art: {missing}")
    for category in sorted({entry["category"] for entry in sprites.values()}):
        ids = [name for name, entry in sprites.items() if entry["category"] == category]
        atlas = pygame.Surface((size[0] * 4, size[1] * ((len(ids) + 3) // 4)), pygame.SRCALPHA)
        for index, name in enumerate(ids):
            rect = [index % 4 * size[0], index // 4 * size[1], *size]
            atlas.blit(images[name], rect[:2])
            sprites[name]["atlas"] = {"file": f"atlases/{category}.png", "rect": rect}
        save(atlas, root / f"atlases/{category}.png")
    manifest = {"schema_version": 1, "name": "PizzaOps Directional Room Furniture", "version": "1.0.0",
                "tile_size": [64, 32], "projection": "screen_x=(map_x-map_y)*32; screen_y=(map_x+map_y)*16",
                "alpha": "binary RGBA", "sprites": sprites, "objects": objects, "animations": {},
                "directions": catalog["directions"], "missing_sources": missing,
                "limitations": ["Static furniture rotations, not animation frames.",
                                "Source views are drawn independently; small surface-detail variations remain.",
                                "Footprints and approach offsets are proposed placement data, not yet wired into the simulation.",
                                "Wall-mounted fixtures need wall elevation; use ground anchor plus an explicit elevation offset."]}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    font = pygame.font.Font(None, 23)
    for category in sorted({obj["category"] for obj in objects.values()}):
        rows = [(name, obj) for name, obj in objects.items() if obj["category"] == category]
        preview = pygame.Surface((1280, 70 + len(rows) * 270))
        preview.fill("#302c32")
        for index, direction in enumerate(catalog["directions"]):
            preview.blit(font.render(direction.upper(), True, "#f3dfb6"), (index * 320 + 140, 20))
        for row, (name, obj) in enumerate(rows):
            for column, direction in enumerate(catalog["directions"]):
                image = images[obj["variants"][direction]]
                bounds = image.get_bounding_rect()
                fitted = pygame.transform.scale_by(image.subsurface(bounds), 2)
                preview.blit(fitted, (column * 320 + 160 - fitted.width // 2, 65 + row * 270 + 205 - fitted.height))
            preview.blit(font.render(name, True, "#f3dfb6"), (14, 65 + row * 270 + 225))
        save(preview, root / f"previews/{category}.png")
    return manifest


def validate(root=ROOT):
    manifest = json.loads((root / "manifest.json").read_text())
    assert not manifest["missing_sources"], manifest["missing_sources"]
    paths, atlas_images, sprite_images = set(), {}, {}
    for name, entry in manifest["sprites"].items():
        path = root / entry["file"]
        assert entry["file"] not in paths
        paths.add(entry["file"])
        image = pygame.image.load(path)
        sprite_images[name] = image
        assert list(image.size) == entry["size"]
        assert set(pygame.image.tobytes(image, "RGBA")[3::4]) == {0, 255}
        bounds = image.get_bounding_rect()
        assert list(bounds) == entry["bounds"]
        assert 0 < bounds.left < bounds.right < image.width
        assert 0 < bounds.top < bounds.bottom < image.height
        assert entry["anchor"] == [80, 144]
        atlas = entry["atlas"]
        if atlas["file"] not in atlas_images:
            atlas_images[atlas["file"]] = pygame.image.load(root / atlas["file"])
        region = atlas_images[atlas["file"]].subsurface(atlas["rect"])
        assert pygame.image.tobytes(region, "RGBA") == pygame.image.tobytes(image, "RGBA")
    assert paths == {str(p.relative_to(root)) for p in (root / "sprites").rglob("*.png")}
    for obj in manifest["objects"].values():
        assert set(obj["variants"]) == {"se", "sw", "ne", "nw"}
        strip = pygame.image.load(root / obj["turnaround"])
        assert strip.size == (640, 192)
        for i, direction in enumerate(obj["direction_order"]):
            region = strip.subsurface((i * 160, 0, 160, 192))
            assert pygame.image.tobytes(region, "RGBA") == pygame.image.tobytes(sprite_images[obj["variants"][direction]], "RGBA")
        assert (root / obj["source"]).is_file() and (root / obj["prompt"]).is_file()
    report = {"status": "passed", "objects": len(manifest["objects"]), "sprites": len(paths), "atlases": len(atlas_images),
              "checks": ["real binary PNG alpha", "all four directions", "fixed canvas and anchors", "no clipped artwork",
                         "manifest inventory", "exact atlas pixels", "exact turnaround pixels", "source and prompt provenance"]}
    (root / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if not args.validate_only:
        manifest = build(allow_missing=args.allow_missing)
        print(f"Built {len(manifest['sprites'])} sprites; missing {len(manifest['missing_sources'])} sources")
    if not args.allow_missing:
        print(json.dumps(validate(), indent=2))
