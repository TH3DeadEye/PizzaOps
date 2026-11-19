"""Small touches that make the restaurant feel alive: shadows, chimney smoke, steam, pop-ups and warm light.

Effects are purely cosmetic. They react to the stage's cues and to what is visibly true (a lit oven, a box on the
counter) and never touch the world. Particles live in projected scene pixels, so they follow the camera's zoom and
pan. Flat shapes keep them in the spirit of the pixel art; light pools are soft and added onto the floor.
"""

import math
import random

import pygame

from viewer.geometry import project

# Offsets from a sprite's ground anchor, in unscaled pixels (measured from the artwork).
CHIMNEY = (6, -87)
OVEN_MOUTH = (-9, -29)
LIGHTS = {  # sprite: (glow offset, glow radius, pool radius, colour)
    "floor_lamp": ((0, -54), 22, 70, (64, 44, 14)),
    "pendant_lamp": ((0, -40), 20, 60, (64, 44, 14)),
    "exterior_lantern": ((0, -40), 16, 50, (60, 40, 12)),
    "pizza_display": ((0, -30), 18, 46, (44, 34, 16)),
    "window": ((0, -31), 20, 64, (26, 30, 38)),
}
SMOKE = (196, 188, 196)
STEAM = (250, 246, 236)
DUST = (214, 190, 150)
POP_ICONS = {"take": "ui_heart", "fail": "ui_warning"}


def radial(radius, color, falloff=1.6):
    """An additive light: ``color`` at the centre fading to black at ``radius`` (built small, then smoothed up)."""
    size = 48
    small = pygame.Surface((size, size))
    for y in range(size):
        for x in range(size):
            d = math.hypot(x + 0.5 - size / 2, y + 0.5 - size / 2) / (size / 2)
            k = max(0.0, 1.0 - d) ** falloff
            small.set_at((x, y), (round(color[0] * k), round(color[1] * k), round(color[2] * k)))
    return pygame.transform.smoothscale(small, (max(2, radius * 2), max(2, radius * 2)))


def vignette(size, strength=110):
    small = pygame.Surface((64, 50), pygame.SRCALPHA)
    for y in range(50):
        for x in range(64):
            d = math.hypot((x + 0.5) / 64 - 0.5, (y + 0.5) / 50 - 0.5) / math.hypot(0.5, 0.5)
            small.set_at((x, y), (12, 8, 14, round(strength * max(0.0, d - 0.45) / 0.55)))
    return pygame.transform.smoothscale(small, size)


class Effects:
    def __init__(self, pack, view, kit, scene):
        self.pack, self.view, self.kit = pack, view, kit
        self.rng = random.Random("pizzaops-fx")
        self.particles = []  # [x, y, vx, vy, age, life, r0, r1, rgb, alpha]
        self.pops = []       # [sprite, x, y, age, life]
        self.sparkles = []   # [x, y, age]
        self.lighting = True
        self.shake = 0.0
        self.flicker = 1.0
        self._clocks = {}
        self._cache = {}
        self.oven = next((obj for obj in scene["objects"] if obj.get("animation") == "oven/lit"), None)
        self.lights = []
        for obj in scene["objects"]:
            spec = LIGHTS.get(obj["sprite"])
            if spec:
                base = project(obj["position"], elevation=obj.get("elevation", 0))
                ground = project(obj["position"]) if obj.get("layer") != "back" else project((obj["position"][0] + 0.9, obj["position"][1]))
                (dx, dy), glow, pool, color = spec
                self.lights.append(((base[0] + dx, base[1] + dy), glow, ground, pool, color))

    # ------------------------------------------------------------------ helpers

    def screen(self, point):
        return (self.view.origin[0] + point[0] * self.view.scale, self.view.origin[1] + point[1] * self.view.scale)

    def cached(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    def every(self, name, seconds, dt):
        """True once per ``seconds`` for the named emitter."""
        self._clocks[name] = self._clocks.get(name, 0.0) + dt
        if self._clocks[name] >= seconds:
            self._clocks[name] -= seconds
            return True
        return False

    def emit(self, x, y, *, vx=0.0, vy=-12.0, life=2.0, r0=2.0, r1=5.0, rgb=SMOKE, alpha=120):
        if len(self.particles) < 240:
            self.particles.append([x, y, vx, vy, 0.0, life, r0, r1, rgb, alpha])

    # ------------------------------------------------------------------ input

    def cue(self, name, position):
        x, y = project(position)
        if name in POP_ICONS:
            self.pops.append([POP_ICONS[name], x, y - 66, 0.0, 1.1])
        elif name == "bell":
            self.sparkles.append([x, y - 44, 0.0])
        elif name == "sting":
            self.shake = 0.3
        elif name in ("restock", "bin"):
            for _ in range(7):
                self.emit(x + self.rng.uniform(-6, 6), y - 4, vx=self.rng.uniform(-16, 16), vy=self.rng.uniform(-10, -3),
                          life=0.8, r0=1.5, r1=3.0, rgb=DUST, alpha=150)

    def update(self, dt, world, oven_lit):
        if self.oven is not None and oven_lit:
            base = project(self.oven["position"])
            if self.every("smoke", 0.22, dt):
                self.emit(base[0] + CHIMNEY[0] + self.rng.uniform(-1, 1), base[1] + CHIMNEY[1],
                          vx=self.rng.uniform(2, 7), vy=self.rng.uniform(-16, -11), life=2.6, r0=1.5, r1=6.0, alpha=110)
        for prop in world.stage.props.values():
            if prop.fading >= 0 and self.every(f"steam:{prop.order_id}", 0.3, dt):
                x, y = project(prop.position, elevation=prop.elevation)
                self.emit(x + self.rng.uniform(-3, 3), y - 9, vx=self.rng.uniform(-2, 2), vy=-10, life=1.7, r0=1.5, r1=4.0,
                          rgb=STEAM, alpha=160)
        for actor in world.everyone():
            if actor.carrying == "pizza_box" or (actor.action == "carry" and actor.character == "chef"):
                if self.every(f"steam:{id(actor)}", 0.5, dt):
                    x, y = project(actor.position)
                    self.emit(x + self.rng.uniform(-3, 3), y - 34, vx=self.rng.uniform(-2, 2), vy=-9, life=1.1, r0=1.0,
                              r1=2.5, rgb=STEAM, alpha=90)
        for particle in self.particles:
            particle[0] += (particle[2] + math.sin(particle[4] * 3 + particle[1]) * 2.5) * dt
            particle[1] += particle[3] * dt
            particle[4] += dt
        self.particles = [particle for particle in self.particles if particle[4] < particle[5]]
        for pop in self.pops:
            pop[3] += dt
        self.pops = [pop for pop in self.pops if pop[3] < pop[4]]
        for sparkle in self.sparkles:
            sparkle[2] += dt
        self.sparkles = [sparkle for sparkle in self.sparkles if sparkle[2] < 0.45]
        self.shake = max(0.0, self.shake - dt)
        self.flicker += (self.rng.uniform(0.82, 1.0) - self.flicker) * min(1.0, dt * 10)

    def offset(self):
        if self.shake <= 0:
            return (0, 0)
        return (self.rng.choice((-2, 2)), self.rng.choice((-1, 1)))

    # ------------------------------------------------------------------ drawing

    def underlay(self, world, oven_lit):
        """Paint between the floor and anything standing on it: light pools, then soft shadows."""
        def paint(target):
            scale = self.view.scale
            if self.lighting:
                for _, _, ground, pool, color in self.lights:
                    self._light(target, ground, pool, color, flatten=2)
                if oven_lit and self.oven is not None:
                    base = project(self.oven["position"])
                    level = round(self.flicker * 20) / 20  # quantised: each level's glow is cached once
                    self._light(target, (base[0] + OVEN_MOUTH[0] - 6, base[1] + 12), 74,
                                tuple(round(channel * level) for channel in (70, 36, 10)), flatten=2)
            shadow = self.cached(("shadow", scale), lambda: self._shadow(scale))
            for actor in world.everyone():
                if actor.gone or actor.alpha <= 0.05:
                    continue
                x, y = self.screen(project(actor.position))
                image = shadow
                if actor.alpha < 1:
                    image = shadow.copy()
                    image.set_alpha(round(255 * actor.alpha))
                target.blit(image, image.get_rect(center=(round(x), round(y) + scale)))
        return paint

    def _shadow(self, scale):
        surface = pygame.Surface((22 * scale, 8 * scale), pygame.SRCALPHA)
        pygame.draw.ellipse(surface, (24, 14, 20, 62), surface.get_rect())
        pygame.draw.ellipse(surface, (24, 14, 20, 44), surface.get_rect().inflate(-8 * scale, -3 * scale))
        return surface

    def _light(self, target, point, radius, color, flatten=1):
        scale = self.view.scale
        key = ("light", radius, color, flatten, scale)
        glow = self.cached(key, lambda: pygame.transform.smoothscale(radial(radius * scale, color),
                                                                      (radius * 2 * scale, radius * 2 * scale // flatten)))
        x, y = self.screen(point)
        target.blit(glow, glow.get_rect(center=(round(x), round(y))), special_flags=pygame.BLEND_RGB_ADD)

    def overlay(self, target, world, oven_lit):
        """Draw above the room: glows, smoke and steam, pop-ups, sparkles and the vignette."""
        scale = self.view.scale
        if self.lighting:
            for glow_point, glow, _, _, color in self.lights:
                self._light(target, glow_point, glow, tuple(channel * 2 for channel in color))
            if oven_lit and self.oven is not None:
                base = project(self.oven["position"])
                level = round(self.flicker * 20) / 20  # quantised: each level's glow is cached once
                self._light(target, (base[0] + OVEN_MOUTH[0], base[1] + OVEN_MOUTH[1]), 26,
                            tuple(round(channel * level) for channel in (120, 64, 16)))
        for x, y, _, _, age, life, r0, r1, rgb, alpha in self.particles:
            t = age / life
            radius = max(1, round((r0 + (r1 - r0) * t) * scale))
            level = round(alpha * (1 - t) ** 1.3 / 32)  # 8 alpha steps keep the cache small
            if level <= 0:
                continue
            puff = self.cached(("puff", radius, rgb, level), lambda: self._puff(radius, rgb, level * 32))
            px, py = self.screen((x, y))
            target.blit(puff, (round(px) - radius, round(py) - radius))
        for sprite, x, y, age, life in self.pops:
            rise = 22 * (1 - (1 - min(1.0, age / life)) ** 2)
            alpha = round(255 * min(1.0, age / 0.12, (life - age) / 0.3))
            px, py = self.screen((x, y - rise))
            badge = self.cached(("badge", sprite), lambda: self._badge(sprite))
            if alpha < 255:
                badge = badge.copy()
                badge.set_alpha(max(0, alpha))
            target.blit(badge, (round(px) - 12, round(py) - 12))
        for x, y, age in self.sparkles:
            px, py = self.screen((x, y))
            size = round((3 + 5 * math.sin(math.pi * age / 0.45)) * scale)
            for dx, dy in ((size, 0), (0, size)):
                pygame.draw.line(target, (255, 236, 170), (px - dx, py - dy), (px + dx, py + dy), max(1, scale))
        if self.lighting:
            shade = self.cached(("vignette", self.view.viewport.size), lambda: vignette(self.view.viewport.size))
            target.blit(shade, self.view.viewport.topleft)

    def _badge(self, sprite):
        """An icon on a small paper tag, readable over any floor or counter."""
        surface = pygame.Surface((26, 26), pygame.SRCALPHA)
        self.kit.frame(surface, (0, 0, 24, 24), "ui_tooltip", shadow=True)
        icon = self.kit.icon(sprite)
        surface.blit(icon, icon.get_rect(center=(12, 12)))
        return surface

    @staticmethod
    def _puff(radius, rgb, alpha):
        surface = pygame.Surface((radius * 2 + 1, radius * 2 + 1), pygame.SRCALPHA)
        pygame.draw.circle(surface, (*rgb, alpha), (radius, radius), radius)
        return surface
