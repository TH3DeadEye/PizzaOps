"""Manifest-based PNG loading and time-based animation."""

import json
from pathlib import Path

import pygame

DEFAULT_ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets" / "pizzaops"


class AssetPack:
    def __init__(self, root=DEFAULT_ASSET_ROOT, use_atlas=False):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "manifest.json").read_text())
        self.sprites = self.manifest["sprites"]
        self.animations = self.manifest["animations"]
        self.use_atlas = use_atlas
        self._images = {}
        self._atlases = {}
        self._scaled = {}

    def image(self, sprite_id, scale=1):
        if not isinstance(scale, int) or scale < 1:
            raise ValueError("Pixel scale must be a positive integer")
        if sprite_id not in self._images:
            entry = self.sprites[sprite_id]
            if self.use_atlas:
                atlas = entry["atlas"]
                if atlas["file"] not in self._atlases:
                    self._atlases[atlas["file"]] = pygame.image.load(self.root / atlas["file"]).convert_alpha()
                image = self._atlases[atlas["file"]].subsurface(atlas["rect"]).copy()
            else:
                image = pygame.image.load(self.root / entry["file"]).convert_alpha()
            self._images[sprite_id] = image
        if scale == 1:
            return self._images[sprite_id]
        key = (sprite_id, scale)
        if key not in self._scaled:
            image = self._images[sprite_id]
            self._scaled[key] = pygame.transform.scale(image, (image.get_width() * scale, image.get_height() * scale))
        return self._scaled[key]

    def draw(self, target, sprite_id, point, scale=1):
        anchor = self.sprites[sprite_id]["anchor"]
        return target.blit(self.image(sprite_id, scale), (round(point[0] - anchor[0] * scale), round(point[1] - anchor[1] * scale)))


class Animator:
    def __init__(self, pack, clip):
        self.pack = pack
        self.clip = clip
        self.elapsed_ms = 0.0

    def play(self, clip):
        if clip not in self.pack.animations:
            raise KeyError(f"Unknown animation: {clip}")
        if clip != self.clip:
            self.clip = clip
            self.elapsed_ms = 0.0

    def update(self, delta_ms):
        if delta_ms < 0:
            raise ValueError("Animation delta cannot be negative")
        data = self.pack.animations[self.clip]
        total = sum(data["durations_ms"])
        elapsed = self.elapsed_ms + delta_ms
        self.elapsed_ms = elapsed % total if data["loop"] else min(elapsed, total - 0.001)

    @property
    def frame_id(self):
        data = self.pack.animations[self.clip]
        elapsed = self.elapsed_ms
        for frame, duration in zip(data["frames"], data["durations_ms"], strict=True):
            if elapsed < duration:
                return frame
            elapsed -= duration
        return data["frames"][-1]
