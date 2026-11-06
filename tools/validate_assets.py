"""Validate every runtime PNG, metadata link, strip and atlas region."""

import json
import os
from collections import Counter
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

ROOT = Path(__file__).resolve().parents[1] / "assets" / "pizzaops"


def validate(root=ROOT):
    pygame.display.init()
    pygame.display.set_mode((1, 1))
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["schema_version"] == 1
    images = {}
    atlases = {}
    strips = {}
    files = set()
    for name, entry in manifest["sprites"].items():
        image = pygame.image.load(root / entry["file"])
        images[name] = image
        assert entry["file"] not in files, f"Duplicate file: {name}"
        files.add(entry["file"])
        assert list(image.get_size()) == entry["size"], name
        assert image.get_masks()[3], f"Missing alpha: {name}"
        alpha = set(pygame.image.tobytes(image, "RGBA")[3::4])
        assert alpha == {0, 255}, f"Nonbinary, empty or opaque alpha: {name}, {alpha}"
        assert list(image.get_bounding_rect()) == entry["bounds"], name
        assert all(0 <= anchor <= size for anchor, size in zip(entry["anchor"], entry["size"], strict=True)), name
        if entry["category"] == "characters":
            assert entry["size"] == [48, 64] and entry["feet"] == [24, 60], name
            bounds = image.get_bounding_rect()
            assert 0 < bounds.left and bounds.right < 48 and 0 < bounds.top and bounds.bottom == 60, f"Clipped/unregistered: {name} {bounds}"
        atlas = entry["atlas"]
        if atlas["file"] not in atlases:
            atlases[atlas["file"]] = pygame.image.load(root / atlas["file"])
        region = atlases[atlas["file"]].subsurface(atlas["rect"])
        assert pygame.image.tobytes(image, "RGBA") == pygame.image.tobytes(region, "RGBA"), f"Atlas mismatch: {name}"
    actual = {str(path.relative_to(root)) for path in (root / "sprites").rglob("*.png")}
    assert actual == files, f"Unindexed PNG files: {actual ^ files}"
    for name, clip in manifest["animations"].items():
        assert len(clip["frames"]) == len(clip["durations_ms"]) > 1, name
        assert all(duration > 0 for duration in clip["durations_ms"]), name
        strip = pygame.image.load(root / clip["strip"])
        strips[name] = strip
        width, height = clip["frame_size"]
        assert strip.get_size() == (width * len(clip["frames"]), height), name
        distinct = set()
        for index, frame in enumerate(clip["frames"]):
            entry = manifest["sprites"][frame]
            assert entry["anchor"] == clip["anchor"] and entry["size"] == clip["frame_size"], name
            frame_bytes = pygame.image.tobytes(images[frame], "RGBA")
            distinct.add(frame_bytes)
            assert frame_bytes == pygame.image.tobytes(strip.subsurface((index * width, 0, width, height)), "RGBA"), name
        assert len(distinct) >= 2, f"Animation has no visible change: {name}"
    scene = json.loads((root / "scene.json").read_text())
    for obj in scene["floors"] + scene["objects"]:
        assert obj["sprite"] in images
        assert all(0 <= value < bound for value, bound in zip(obj["position"], scene["size"], strict=True))
        if obj.get("animation"):
            assert obj["animation"] in manifest["animations"]
    for actor in scene["characters"]:
        assert f"{actor['character']}/{actor['action']}/{actor['direction']}" in manifest["animations"]
    report = {"status": "passed", "sprites": len(images), "animations": len(strips), "atlases": len(atlases),
              "categories": dict(Counter(entry["category"] for entry in manifest["sprites"].values())),
              "checks": ["RGBA binary alpha", "individual PNG inventory", "dimensions", "anchors and feet registration",
                         "character clipping", "frame duration counts", "visible animation changes", "atlas pixel equality",
                         "strip pixel equality", "scene references and bounds"]}
    return report


if __name__ == "__main__":
    report = validate()
    (ROOT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
