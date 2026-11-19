"""A camera over the restaurant: integer zoom (pixel art stays crisp), panning, and a smooth glide between the two.

Positions are in projected scene pixels (``geometry.project`` of a tile, before scaling). The camera keeps the
restaurant on screen: at the zoom where everything fits it stays centred; when zoomed in it can pan up to the edges.
"""

import pygame

PAN_SPEED = 520  # screen pixels per second while a pan key is held
GLIDE = 12.0     # how quickly the view catches up with its target (per second)


class Camera:
    def __init__(self, viewport, bounds, fit_scale, max_zoom=2):
        self.viewport = pygame.Rect(viewport)
        self.bounds = pygame.Rect(bounds)
        self.min_zoom = max(1, fit_scale)
        self.max_zoom = max(self.min_zoom, max_zoom)
        self.zoom = self.min_zoom
        self.center = [float(self.bounds.centerx), float(self.bounds.centery)]
        self.target = list(self.center)

    @property
    def origin(self):
        return (round(self.viewport.centerx - self.center[0] * self.zoom),
                round(self.viewport.centery - self.center[1] * self.zoom))

    def scene_point(self, screen_point):
        """The projected scene point currently under ``screen_point``."""
        x, y = self.origin
        return ((screen_point[0] - x) / self.zoom, (screen_point[1] - y) / self.zoom)

    def _clamped(self, point):
        clamped = []
        for axis, value in enumerate(point):
            low, high = (self.bounds.left, self.bounds.right) if axis == 0 else (self.bounds.top, self.bounds.bottom)
            half = (self.viewport.width if axis == 0 else self.viewport.height) / (2 * self.zoom)
            if high - low <= 2 * half:
                clamped.append((low + high) / 2)
            else:
                clamped.append(min(max(value, low + half), high - half))
        return clamped

    def zoom_at(self, screen_point, zoom):
        """Change zoom keeping whatever is under ``screen_point`` exactly there."""
        zoom = max(self.min_zoom, min(self.max_zoom, int(zoom)))
        if zoom == self.zoom:
            return
        anchor = self.scene_point(screen_point)
        self.zoom = zoom
        offset = ((screen_point[0] - self.viewport.centerx) / zoom, (screen_point[1] - self.viewport.centery) / zoom)
        self.center = self._clamped((anchor[0] - offset[0], anchor[1] - offset[1]))
        self.target = list(self.center)

    def toggle_zoom(self, screen_point=None):
        self.zoom_at(screen_point or self.viewport.center, self.max_zoom if self.zoom < self.max_zoom else self.min_zoom)

    def pan(self, dx, dy):
        """Move the view by a screen-pixel offset (positive dx shows more of the right)."""
        self.target = self._clamped((self.target[0] + dx / self.zoom, self.target[1] + dy / self.zoom))

    def recenter(self):
        self.zoom = self.min_zoom
        self.center = [float(self.bounds.centerx), float(self.bounds.centery)]
        self.target = list(self.center)

    def update(self, seconds, keys=None):
        """Glide towards the target; ``keys`` maps held directions to {-1, 0, 1} pairs for keyboard panning."""
        if keys:
            self.pan(keys[0] * PAN_SPEED * seconds, keys[1] * PAN_SPEED * seconds)
        blend = min(1.0, seconds * GLIDE)
        self.center = [current + (goal - current) * blend for current, goal in zip(self.center, self.target)]
