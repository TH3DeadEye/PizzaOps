"""Shared room drawing: one camera, one scale, one depth order for props and people."""

import pygame

from viewer.assets import Animator
from viewer.geometry import depth_key, project

SIZE = (1280, 840)
STAGE = pygame.Rect(8, 88, 1024, 674)
PANEL_X = 1050


class SceneView:
    def __init__(self, pack, scene, viewport=STAGE):
        self.pack, self.scene = pack, scene
        self.viewport = pygame.Rect(viewport)
        bounds = None
        for obj in scene["floors"] + scene["objects"]:
            entry = pack.sprites[obj["sprite"]]
            px, py = project(obj["position"], elevation=obj.get("elevation", 0))
            bx, by, width, height = entry["bounds"]
            rect = pygame.Rect(px - entry["anchor"][0] + bx, py - entry["anchor"][1] + by, width, height)
            bounds = rect if bounds is None else bounds.union(rect)
        self.scale = max(1, min(2, self.viewport.width // bounds.width, self.viewport.height // bounds.height))
        self.origin = (self.viewport.centerx - bounds.centerx * self.scale,
                       self.viewport.centery - bounds.centery * self.scale)
        self.oven = Animator(pack, "oven/lit")
        self._grey = {}
        self.label_font = pygame.font.Font(None, 17)

    def point(self, position, elevation=0):
        x, y = project(position, elevation=elevation)
        return self.origin[0] + x * self.scale, self.origin[1] + y * self.scale

    def sprite(self, target, sprite, position, elevation=0, alpha=1.0, grey=False):
        if alpha >= 1.0 and not grey:
            return self.pack.draw(target, sprite, self.point(position, elevation), self.scale)
        if alpha <= 0.0:
            return None
        image = self.pack.image(sprite, self.scale)
        if grey:  # a character whose service is offline
            key = (sprite, self.scale)
            if key not in self._grey:
                self._grey[key] = pygame.transform.grayscale(image)
            image = self._grey[key]
        if alpha < 1.0:
            image = image.copy()
            image.set_alpha(round(255 * alpha))
        anchor = self.pack.sprites[sprite]["anchor"]
        x, y = self.point(position, elevation)
        return target.blit(image, (round(x - anchor[0] * self.scale), round(y - anchor[1] * self.scale)))

    def draw(self, target, actors=(), *, grid=False, oven_lit=True, labels=True):
        clip = target.get_clip()
        target.set_clip(self.viewport)
        for tile in sorted(self.scene["floors"], key=depth_key):
            self.sprite(target, tile["sprite"], tile["position"])
            if grid:
                point = self.point(tile["position"])
                color = "#ab524b" if tile["position"] in self.scene.get("blocked", []) else "#497d69"
                pygame.draw.circle(target, color, point, 3 * self.scale)
        for obj in self.scene["objects"]:
            if obj.get("layer") in ("ground", "back"):
                self.sprite(target, obj["sprite"], obj["position"], obj.get("elevation", 0))
        records = [o for o in self.scene["objects"] if o.get("layer") not in ("ground", "back")]
        for obj in sorted(records + list(actors), key=depth_key):
            if obj.get("animation") == "oven/lit":
                sprite = self.oven.frame_id if oven_lit else "oven_off"
            else:
                sprite = obj["sprite"]
            alpha = obj.get("alpha", 1.0)
            self.sprite(target, sprite, obj["position"], obj.get("elevation", 0), alpha, obj.get("grey", False))
            if obj.get("carrying"):
                self.sprite(target, obj["carrying"], obj["position"], 18, alpha)
        if labels:
            for label in self.scene.get("labels", []):
                text = self.label_font.render(label["text"], True, "#f3dfb6")
                rect = text.get_rect(center=self.point(label["position"]))
                pygame.draw.rect(target, "#493a37", rect.inflate(12, 8), border_radius=4)
                target.blit(text, rect)
        target.set_clip(clip)
