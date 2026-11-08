"""Draw the restaurant: the authored scene, the live actors, speech, and a HUD of service truth."""

import json
from pathlib import Path

import pygame

from viewer.assets import Animator, AssetPack, DEFAULT_ASSET_ROOT
from viewer.geometry import depth_key, project
from viewer.updates import SERVICES

SIZE = (900, 600)
INK = "#49343b"
CREAM = "#f3dfb6"
MUTED = "#c8ad8d"
GOOD = "#9db59a"
BAD = "#c97b7b"
WARN = "#d4a574"
PANEL = "#24272c"
MOOD_ICON = {"CALM": "ui_mood_calm", "TENSE": "ui_mood_busy", "SUSPICIOUS": "ui_mood_stressed", "FURIOUS": "ui_warning"}
STATUS_COLOR = {"LIVE": GOOD, "SYNCING": WARN, "DISCONNECTED": BAD, "RUN_MISMATCH": BAD}


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
        self.origin = tuple(self.scene["origin"])
        self.font = pygame.font.Font(None, 18)
        self.small = pygame.font.Font(None, 16)
        self.title = pygame.font.Font(None, 32)
        self.tiny = pygame.font.Font(None, 14)
        self.oven = Animator(pack, "oven/lit")
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
            animator.update(dt_ms)
        self.animators = {key: animator for key, animator in self.animators.items() if key in living}

    def draw(self, target, world, *, mode, paused, speed, caption):
        target.fill("#302c32")
        self._banner(target, world, mode, paused, speed, caption)
        self._scene(target, world)
        self._hud(target, world)
        self._ticker(target, world)

    def _banner(self, target, world, mode, paused, speed, caption):
        pygame.draw.rect(target, PANEL, (0, 0, SIZE[0], 54))
        self.text(target, "PIZZAOPS", (18, 8), self.title)
        self.text(target, caption, (148, 18), self.small, MUTED)
        x = 430
        for name in SERVICES:
            view = world.services[name]
            color = STATUS_COLOR.get(view.state, MUTED)
            pygame.draw.circle(target, color, (x + 6, 20), 5)
            self.text(target, f"{name[:3].upper()} {view.state}", (x + 16, 12), self.tiny, color)
            x += 110
        self.text(target, "PAUSED" if paused else f"{speed:g}x", (830, 12), self.small, WARN if paused else GOOD)
        self.text(target, "SPACE pause   +/- speed   ESC quit", (18, 34), self.tiny, MUTED)

    def _scene(self, target, world):
        origin = self.origin
        for tile in sorted(self.scene["floors"], key=depth_key):
            self.pack.draw(target, tile["sprite"], project(tile["position"], origin))
        for obj in self.scene["objects"]:
            if obj.get("layer") in ("ground", "back"):
                self.pack.draw(target, obj["sprite"], project(obj["position"], origin, obj.get("elevation", 0)))
        drawables = [obj for obj in self.scene["objects"] if obj.get("layer") not in ("ground", "back")]
        actors = [{"position": list(actor.position), "actor": actor} for actor in world.everyone() if not actor.gone]
        for record in sorted(drawables + actors, key=depth_key):
            if "actor" in record:
                self._actor(target, record["actor"])
            else:
                sprite = self.oven.frame_id if record.get("animation") else record["sprite"]
                self.pack.draw(target, sprite, project(record["position"], origin, record.get("elevation", 0)))

    def _actor(self, target, actor):
        animator = self.animators.get(id(actor))
        sprite = animator.frame_id if animator else f"{actor.character}_idle_se_00"
        feet = project(actor.position, self.origin)
        self.pack.draw(target, sprite, feet)
        if actor.carrying:
            self.pack.draw(target, actor.carrying, (feet[0], feet[1] - 18))
        if actor.speech:
            self._bubble(target, feet, actor.speech)

    def _bubble(self, target, feet, speech):
        lines = wrap(speech)
        width = max(self.tiny.size(line)[0] for line in lines) + 12
        height = 6 + 12 * len(lines)
        left, top = feet[0] - width // 2, feet[1] - 78 - height
        left = max(8, min(left, SIZE[0] - width - 8))
        top = max(58, top)
        pygame.draw.rect(target, CREAM, (left, top, width, height), border_radius=6)
        pygame.draw.polygon(target, CREAM, [(feet[0] - 5, top + height), (feet[0] + 5, top + height), (feet[0], top + height + 7)])
        for index, line in enumerate(lines):
            self.text(target, line, (left + 6, top + 3 + index * 12), self.tiny, INK)

    def _hud(self, target, world):
        pygame.draw.rect(target, PANEL, (12, 64, 176, 250), border_radius=6)
        mood = world.mood_label
        self.pack.draw(target, MOOD_ICON.get(mood, "ui_mood_calm"), (34, 88))
        self.text(target, mood.title(), (50, 80), self.small)
        if world.mood:
            self.text(target, f"trust {world.mood['trust']}  patience {world.mood['patience']}", (22, 100), self.tiny, MUTED)
        self.text(target, "STOCK", (22, 122), self.tiny, MUTED)
        y = 138
        for name, quantity in (world.stock or {}).items():
            color = BAD if quantity <= 0 else WARN if quantity < 10 else CREAM
            self.text(target, f"{name:<10} {quantity:>4}", (22, y), self.tiny, color)
            y += 14
        if world.stock_uncertain:
            self.text(target, "stock may be stale", (22, y), self.tiny, WARN)
            y += 14
        counts = world.counters
        self.text(target, f"open {counts['open']}  ready {counts['ready']}", (22, y + 8), self.tiny, GOOD)
        self.text(target, f"failed {counts['failed']}  unknown {counts['unknown']}", (22, y + 22), self.tiny, BAD if counts["failed"] or counts["unknown"] else MUTED)
        pygame.draw.rect(target, PANEL, (712, 64, 176, 250), border_radius=6)
        self.text(target, "ORDERS", (724, 72), self.tiny, MUTED)
        y = 90
        for order in world.recent_orders(8):
            color = {"READY": GOOD, "FAILED": BAD, "UNKNOWN": WARN}.get(order["status"], CREAM)
            pizza = f"{order['pizzaType'][:3]} x{order['quantity']}"
            self.text(target, f"{order['status']:<10} {pizza}", (724, y), self.tiny, color)
            y += 14
            if order.get("failureReason"):
                self.text(target, order["failureReason"], (732, y), self.tiny, BAD)
                y += 14

    def _ticker(self, target, world):
        pygame.draw.rect(target, PANEL, (0, 520, SIZE[0], 80))
        y = 528
        for line in list(world.ticker)[-5:]:
            who = (line["service"] or "")[:3].upper()
            self.text(target, f"{who:<3} {line['type']:<22} {line['detail']}", (16, y), self.tiny, MUTED)
            y += 14
