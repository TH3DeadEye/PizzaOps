"""Shared room drawing: one camera, one scale, one depth order for props and people.

By default the view fits the whole room into its viewport. A caller with a camera passes its origin and integer
scale each frame with ``set_camera``. Floors, rugs and rear walls never move, so they are drawn once per scale into
a cached layer; everything that can stand in front of something else is depth-sorted every frame.
"""

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
        self.bounds = bounds
        self.fit_scale = max(1, min(2, self.viewport.width // bounds.width, self.viewport.height // bounds.height))
        self.fit_origin = (self.viewport.centerx - bounds.centerx * self.fit_scale,
                           self.viewport.centery - bounds.centery * self.fit_scale)
        self.scale, self.origin = self.fit_scale, self.fit_origin
        self.oven = Animator(pack, "oven/lit")
        self._grey = {}
        self._static = {}
        self.label_font = pygame.font.Font(None, 17)
        self.label_antialias = True

    def set_camera(self, origin, scale):
        self.origin, self.scale = origin, scale

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

    def _static_layer(self):
        """Floors, ground decor and rear walls at the current scale, drawn once."""
        layer = self._static.get(self.scale)
        if layer is None:
            layer = pygame.Surface((self.bounds.width * self.scale, self.bounds.height * self.scale), pygame.SRCALPHA)
            origin = self.origin
            self.origin = (-self.bounds.x * self.scale, -self.bounds.y * self.scale)
            try:
                for tile in sorted(self.scene["floors"], key=depth_key):
                    self.sprite(layer, tile["sprite"], tile["position"])
                for obj in self.scene["objects"]:
                    if obj.get("layer") in ("ground", "back"):
                        self.sprite(layer, obj["sprite"], obj["position"], obj.get("elevation", 0))
            finally:
                self.origin = origin
            self._static[self.scale] = layer
        return layer

    def draw(self, target, actors=(), *, grid=False, oven_lit=True, labels=True, underlay=None):
        """Draw the room and ``actors`` (records with position, sprite and optional elevation, alpha, grey,
        carrying). ``underlay(target)`` paints between the floor and anything standing on it (shadows, light)."""
        clip = target.get_clip()
        target.set_clip(self.viewport)
        target.blit(self._static_layer(), (round(self.origin[0] + self.bounds.x * self.scale),
                                           round(self.origin[1] + self.bounds.y * self.scale)))
        if grid:
            blocked = self.scene.get("blocked", [])
            for tile in self.scene["floors"]:
                color = "#ab524b" if tile["position"] in blocked else "#497d69"
                pygame.draw.circle(target, color, self.point(tile["position"]), 3 * self.scale)
        if underlay is not None:
            underlay(target)
        actors = list(actors)
        records = [o for o in self.scene["objects"] if o.get("layer") not in ("ground", "back")]
        for obj in sorted(records + actors, key=depth_key):
            if obj.get("animation") == "oven/lit":
                sprite = self.oven.frame_id if oven_lit else "oven_off"
            else:
                sprite = obj["sprite"]
            alpha = obj.get("alpha", 1.0)
            self.sprite(target, sprite, obj["position"], obj.get("elevation", 0), alpha, obj.get("grey", False))
            if obj.get("carrying"):
                self.sprite(target, obj["carrying"], obj["position"], 18, alpha)
        if labels:
            self._labels(target, actors)
        target.set_clip(clip)

    def _labels(self, target, actors):
        """Room names stay readable above the furniture, but fade to a ghost when someone stands beneath one."""
        occupied = []
        for record in actors:
            entry = self.pack.sprites[record["sprite"]]
            x, y = self.point(record["position"], record.get("elevation", 0))
            bx, by, width, height = entry["bounds"]
            occupied.append(pygame.Rect(x + (bx - entry["anchor"][0]) * self.scale, y + (by - entry["anchor"][1]) * self.scale,
                                        width * self.scale, height * self.scale))
        for label in self.scene.get("labels", []):
            text = self.label_font.render(label["text"], self.label_antialias, "#f3dfb6")
            rect = text.get_rect(center=self.point(label["position"]))
            plate = rect.inflate(12, 8)
            layer = pygame.Surface(plate.size, pygame.SRCALPHA)
            pygame.draw.rect(layer, "#493a37", layer.get_rect(), border_radius=4)
            layer.blit(text, (6, 4))
            if plate.collidelist(occupied) != -1:
                layer.set_alpha(60)
            target.blit(layer, plate)
