"""Draw the restaurant: the authored scene, the live actors, speech, and a HUD of service truth."""

import json
from pathlib import Path

import pygame

from viewer.assets import Animator, AssetPack, DEFAULT_ASSET_ROOT
from viewer.scene import SceneView, SIZE, PANEL_X, STAGE
from viewer.updates import SERVICES

INK = "#49343b"
CREAM = "#f3dfb6"
MUTED = "#c8ad8d"
GOOD = "#9db59a"
BAD = "#c97b7b"
WARN = "#d4a574"
PANEL = "#24272c"
MOOD_ICON = {"CALM": "ui_mood_calm", "TENSE": "ui_mood_busy", "SUSPICIOUS": "ui_mood_stressed", "FURIOUS": "ui_warning"}
STATUS_COLOR = {"LIVE": GOOD, "SYNCING": WARN, "DISCONNECTED": BAD, "RUN_MISMATCH": BAD}
EMOTE_ICON = {"warning": "ui_warning", "angry": "ui_mood_stressed", "unknown": "ui_mood_busy"}


def wrap(text, width=28):
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]


class Renderer:
    def __init__(self, pack, scene_path=None):
        self.pack = pack
        self.scene = json.loads((scene_path or pack.root / "scene.json").read_text())
        self.view = SceneView(pack, self.scene)
        self.origin = self.view.origin
        self.font = pygame.font.Font(None, 18)
        self.small = pygame.font.Font(None, 16)
        self.title = pygame.font.Font(None, 32)
        self.tiny = pygame.font.Font(None, 14)
        self.oven = self.view.oven
        self.animators = {}

    def text(self, target, content, point, font=None, color=CREAM):
        target.blit((font or self.font).render(content, True, color), point)

    def update(self, world, dt_ms):
        self.oven.update(dt_ms)
        living = {id(actor) for actor in world.everyone() if not actor.gone}
        for actor in world.everyone():
            if actor.gone:
                continue
            direction = actor.direction if actor.action == "walk" or actor.direction in ("se", "sw") else ("se" if actor.direction == "ne" else "sw")
            clip = f"{actor.character}/{actor.action}/{direction}"
            animator = self.animators.get(id(actor))
            if animator is None:
                animator = Animator(self.pack, clip)
                self.animators[id(actor)] = animator
            animator.play(clip)
            # A hurrying character's legs keep pace with its feet.
            rate = actor.walk_speed * actor.rate / 1.6 if actor.moving else 1.0
            animator.update(dt_ms, rate)
        self.animators = {key: animator for key, animator in self.animators.items() if key in living}

    def draw(self, target, world, *, mode, paused, speed, caption):
        target.fill("#302c32")
        self._banner(target, world, mode, paused, speed, caption)
        self._scene(target, world)
        self._hud(target, world)
        self._ticker(target, world)

    def _banner(self, target, world, mode, paused, speed, caption):
        pygame.draw.rect(target, PANEL, (0, 0, SIZE[0], 72))
        self.text(target, "PIZZAOPS", (18, 8), self.title)
        self.text(target, caption[:55], (148, 18), self.small, MUTED)
        x = 720
        for name in SERVICES:
            view = world.services[name]
            color = STATUS_COLOR.get(view.state, MUTED)
            pygame.draw.circle(target, color, (x + 6, 20), 5)
            self.text(target, f"{name[:3].upper()} {view.state}", (x + 16, 12), self.tiny, color)
            x += 150
        self.text(target, "PAUSED" if paused else f"{speed:g}x", (1210, 12), self.small, WARN if paused else GOOD)
        self.text(target, "SPACE pause   +/- speed   ESC quit", (18, 48), self.small, MUTED)
        self.text(target, "KITCHEN + STOCKROOM / PICKUP + DINING", (720, 48), self.small, MUTED)

    def _scene(self, target, world):
        actors = []
        for actor in world.everyone():
            if actor.gone:
                continue
            animator = self.animators.get(id(actor))
            actors.append({"position": actor.position,
                           "sprite": animator.frame_id if animator else f"{actor.character}_idle_se_00",
                           "carrying": actor.carrying if actor.action != "carry" else None, "alpha": actor.alpha})
        for prop in world.stage.props.values():
            actors.append({"position": prop.position, "sprite": prop.sprite, "elevation": prop.elevation,
                           "depth_bias": prop.depth_bias, "alpha": prop.alpha})
        self.view.draw(target, actors, oven_lit=any(j["status"] == "COOKING" for j in world.jobs.values()))
        for actor in world.everyone():
            if not actor.gone and actor.emote and actor.alpha > 0.5:
                self.pack.draw(target, EMOTE_ICON.get(actor.emote, "ui_warning"), self.view.point(actor.position, 74))
        for actor in world.everyone():
            if not actor.gone and actor.speech:
                self._bubble(target, self.view.point(actor.position), actor.speech)

    def _bubble(self, target, feet, speech):
        lines = wrap(speech)
        width = max(self.tiny.size(line)[0] for line in lines) + 12
        height = 6 + 12 * len(lines)
        left, top = feet[0] - width // 2, feet[1] - 78 - height
        left = max(STAGE.left, min(left, STAGE.right - width))
        top = max(STAGE.top, top)
        pygame.draw.rect(target, CREAM, (left, top, width, height), border_radius=6)
        pygame.draw.polygon(target, CREAM, [(feet[0] - 5, top + height), (feet[0] + 5, top + height), (feet[0], top + height + 7)])
        for index, line in enumerate(lines):
            self.text(target, line, (left + 6, top + 3 + index * 12), self.tiny, INK)

    def _hud(self, target, world):
        x = PANEL_X
        pygame.draw.rect(target, PANEL, (x - 6, 88, 224, 674), border_radius=8)
        mood = world.mood_label
        self.pack.draw(target, MOOD_ICON.get(mood, "ui_mood_calm"), (x + 18, 112))
        self.text(target, mood.title(), (x + 36, 104), self.font)
        if world.mood:
            self.text(target, f"trust {world.mood['trust']}  patience {world.mood['patience']}", (x + 10, 130), self.small, MUTED)
        self.text(target, "STOCK", (x + 10, 164), self.small, MUTED)
        y = 188
        for name, quantity in (world.stock or {}).items():
            color = BAD if quantity <= 0 else WARN if quantity < 10 else CREAM
            self.text(target, f"{name:<10} {quantity:>4}", (x + 10, y), self.small, color)
            y += 20
        if world.stock_uncertain:
            self.text(target, "stock may be stale", (x + 10, y), self.small, WARN)
            y += 20
        counts = world.counters
        self.text(target, f"open {counts['open']}  ready {counts['ready']}", (x + 10, y + 12), self.small, GOOD)
        self.text(target, f"failed {counts['failed']}  unknown {counts['unknown']}", (x + 10, y + 34), self.small, BAD if counts["failed"] or counts["unknown"] else MUTED)
        self.text(target, "ORDERS", (x + 10, 394), self.small, MUTED)
        y = 420
        for order in world.recent_orders(8):
            color = {"READY": GOOD, "FAILED": BAD, "UNKNOWN": WARN}.get(order["status"], CREAM)
            pizza = f"{order['pizzaType'][:3]} x{order['quantity']}"
            self.text(target, f"{order['status']:<10} {pizza}", (x + 10, y), self.small, color)
            y += 20
            if order.get("failureReason"):
                self.text(target, order["failureReason"], (x + 16, y), self.tiny, BAD)
                y += 18

    def _ticker(self, target, world):
        pygame.draw.rect(target, PANEL, (0, 778, SIZE[0], 62))
        y = 784
        for line in list(world.ticker)[-4:]:
            who = (line["service"] or "")[:3].upper()
            self.text(target, f"{who:<3} {line['type']:<22} {line['detail']}", (16, y), self.tiny, MUTED)
            y += 14
