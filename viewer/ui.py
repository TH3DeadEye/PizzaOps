"""The live viewer's look: palette, pixel font, 9-slice frames, bars, icons and portraits.

Everything is built from the pack's own UI sprites (paper panels, bubbles, bars, icons), so the interface matches
the restaurant art. Text uses Pixelify Sans (SIL Open Font License, ``assets/game/fonts``) without antialiasing at
sizes where its strokes land on whole pixels; if the font file is missing, pygame's default font is used instead.
"""

from pathlib import Path

import pygame

GAME_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "game"
FONT_PATH = GAME_ASSETS / "fonts" / "PixelifySans.ttf"

# Palette: paper and ink come from the pack's UI sprites; the darks are the night around the restaurant.
PAPER = "#f3dfb6"
PAPER_DIM = "#e2c99c"
INK = "#49343b"
INK_SOFT = "#7a5f5c"
NIGHT = "#2b2530"
NIGHT_DEEP = "#221d26"
NIGHT_LINE = "#3d3440"
CREAM = PAPER
MUTED = "#c8ad8d"
GOOD = "#9db59a"
BAD = "#d98a7e"
WARN = "#e0b072"
GOOD_INK = "#4f7a4c"
BAD_INK = "#a3473f"
WARN_INK = "#a86a22"
SHADOW = (20, 14, 20, 120)

STYLES = {"small": 13, "body": 14, "bold": 16, "title": 32}

INGREDIENT_ICONS = {"DOUGH": "dough", "SAUCE": "sauce_pot", "CHEESE": "crate_cheese", "PEPPERONI": "pizza_slice", "BASIL": "basil"}
PIZZA_ICONS = {"CHEESE": "pizza_cheese", "PEPPERONI": "pizza_pepperoni", "MARINARA": "pizza_raw"}


class Fonts:
    """Crisp pixel text with a render cache. ``pixel`` is False when falling back to the default font."""

    def __init__(self, path=FONT_PATH):
        self.pixel = Path(path).exists()
        self._fonts = {}
        for style, size in STYLES.items():
            self._fonts[style] = pygame.font.Font(str(path), size) if self.pixel else pygame.font.Font(None, size + 4)
        self._cache = {}

    def font(self, style="body"):
        return self._fonts[style]

    def render(self, text, style="body", color=INK):
        key = (text, style, color)
        surface = self._cache.get(key)
        if surface is None:
            if len(self._cache) > 2000:
                self._cache.clear()
            surface = self._fonts[style].render(text, not self.pixel, color)
            self._cache[key] = surface
        return surface

    def size(self, text, style="body"):
        return self._fonts[style].size(text)

    def line_height(self, style="body"):
        return self._fonts[style].get_linesize()

    def draw(self, target, text, point, style="body", color=INK, align="left"):
        surface = self.render(text, style, color)
        x, y = point
        if align == "center":
            x -= surface.get_width() // 2
        elif align == "right":
            x -= surface.get_width()
        return target.blit(surface, (round(x), round(y)))

    def wrap(self, text, width, style="body", max_lines=3):
        """Words broken into lines no wider than ``width`` pixels; an over-long word gets a line of its own."""
        lines, current = [], ""
        for word in text.split():
            trial = f"{current} {word}".strip()
            if self.size(trial, style)[0] <= width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            words = lines[-1].split()
            while len(words) > 1 and self.size(" ".join(words) + "…", style)[0] > width:
                words.pop()
            lines[-1] = " ".join(words).rstrip(".,!?") + "…"
        return lines


def layout_bubbles(requests, bounds, gap=3):
    """Place speech bubbles without overlap. ``requests`` are preferred ``(x, y, w, h)`` boxes, most important first.

    Each bubble keeps its preferred spot if it is free; otherwise it climbs just above whatever it hits, and if it
    would leave the stage that way it drops below instead. Everything stays inside ``bounds``. Returns the boxes.
    """
    bounds = pygame.Rect(bounds)
    placed = []
    for x, y, w, h in requests:
        x = max(bounds.left, min(x, bounds.right - w))
        y = max(bounds.top, min(y, bounds.bottom - h))
        spot = None
        for direction in (-1, 1):
            rect = pygame.Rect(x, y, w, h)
            for _ in range(len(placed) + 1):
                hits = [other for other in placed if rect.colliderect(other.inflate(gap * 2, gap * 2))]
                if not hits:
                    break
                if direction < 0:
                    rect.bottom = min(other.top for other in hits) - gap
                else:
                    rect.top = max(other.bottom for other in hits) + gap
            if bounds.contains(rect) and not any(rect.colliderect(other.inflate(gap * 2, gap * 2)) for other in placed):
                spot = rect
                break
        placed.append(spot or pygame.Rect(x, y, w, h))
    return [tuple(rect) for rect in placed]


def visible(image):
    """The image cropped to its non-transparent pixels."""
    return image.subsurface(image.get_bounding_rect()).copy()


def nine_slice(image, size, border=3):
    """Stretch ``image`` to ``size`` keeping its ``border``-pixel corners and edges crisp (nearest-neighbour)."""
    width, height = max(size[0], 2 * border + 1), max(size[1], 2 * border + 1)
    source_w, source_h = image.get_size()
    out = pygame.Surface((width, height), pygame.SRCALPHA)
    xs = [(0, border, 0, border), (border, source_w - border, border, width - border), (source_w - border, source_w, width - border, width)]
    ys = [(0, border, 0, border), (border, source_h - border, border, height - border), (source_h - border, source_h, height - border, height)]
    for sx0, sx1, dx0, dx1 in xs:
        for sy0, sy1, dy0, dy1 in ys:
            piece = image.subsurface((sx0, sy0, sx1 - sx0, sy1 - sy0))
            if (dx1 - dx0, dy1 - dy0) != piece.get_size():
                piece = pygame.transform.scale(piece, (dx1 - dx0, dy1 - dy0))
            out.blit(piece, (dx0, dy0))
    return out


class Kit:
    """Cached frames, bars, icons and portraits drawn from the asset pack."""

    def __init__(self, pack, fonts=None):
        self.pack = pack
        self.fonts = fonts or Fonts()
        self._sources = {}
        self._frames = {}
        self._icons = {}
        self._portraits = {}

    def source(self, sprite_id):
        if sprite_id not in self._sources:
            self._sources[sprite_id] = visible(self.pack.image(sprite_id))
        return self._sources[sprite_id]

    def frame(self, target, rect, sprite_id="ui_panel", *, shadow=True, alpha=255):
        rect = pygame.Rect(rect)
        key = (sprite_id, rect.size)
        cached = self._frames.get(key)
        if cached is None:
            if len(self._frames) > 400:
                self._frames.clear()
            surface = nine_slice(self.source(sprite_id), rect.size)
            shade = pygame.Surface(rect.size, pygame.SRCALPHA)
            shade.fill(SHADOW)
            shade.blit(surface, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)  # the frame's silhouette, darkened
            cached = self._frames[key] = (surface, shade)
        surface, shade = cached
        if alpha < 255:
            surface, shade = surface.copy(), shade.copy()
            surface.set_alpha(alpha)
            shade.set_alpha(alpha)  # modulates the shade's own per-pixel alpha
        if shadow:
            target.blit(shade, rect.move(2, 2))
        return target.blit(surface, rect)

    def bar(self, target, rect, fraction, color, *, markers=(), track=PAPER_DIM):
        """A paper-framed gauge filled to ``fraction``; ``markers`` are thresholds drawn as ink ticks."""
        rect = pygame.Rect(rect)
        self.frame(target, rect, "ui_bar_empty", shadow=False)
        inner = rect.inflate(-4, -4)
        pygame.draw.rect(target, track, inner)
        filled = round(inner.width * max(0.0, min(1.0, fraction)))
        if filled:
            pygame.draw.rect(target, color, (inner.x, inner.y, filled, inner.height))
        for marker in markers:
            x = inner.x + round(inner.width * marker)
            pygame.draw.line(target, INK, (x, rect.y - 1), (x, rect.bottom), 1)

    def icon(self, sprite_id, scale=1, max_height=None):
        """The sprite cropped to its pixels; taller than ``max_height`` halves it (nearest-neighbour, still crisp)."""
        key = (sprite_id, scale, max_height)
        if key not in self._icons:
            image = self.source(sprite_id)
            if scale != 1:
                image = pygame.transform.scale(image, (image.get_width() * scale, image.get_height() * scale))
            while max_height and image.get_height() > max_height:
                image = pygame.transform.scale(image, (image.get_width() // 2, image.get_height() // 2))
            self._icons[key] = image
        return self._icons[key]

    def draw_icon(self, target, sprite_id, center, scale=1, alpha=255, max_height=None):
        image = self.icon(sprite_id, scale, max_height)
        if alpha < 255:
            image = image.copy()
            image.set_alpha(alpha)
        return target.blit(image, image.get_rect(center=(round(center[0]), round(center[1]))))

    def portrait(self, character, scale=1):
        """Head and shoulders from the character's first idle frame."""
        key = (character, scale)
        if key not in self._portraits:
            frame = self.pack.image(f"{character}_idle_se_00")
            bounds = frame.get_bounding_rect()
            crop = pygame.Rect(0, 0, 28, 24)
            crop.midtop = (bounds.centerx, bounds.top)
            image = frame.subsurface(crop.clip(frame.get_rect())).copy()
            if scale != 1:
                image = pygame.transform.scale(image, (image.get_width() * scale, image.get_height() * scale))
            self._portraits[key] = image
        return self._portraits[key]

    def lamp(self, target, center, color, radius=4):
        pygame.draw.circle(target, INK, center, radius + 1)
        pygame.draw.circle(target, color, center, radius)
