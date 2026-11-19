"""Draw the live restaurant: the cast on stage, speech bubbles, the story strip and a sidebar of the services' truth.

The 1280 × 840 canvas has a slim top bar, the stage on the left and paper cards on the right. The stage shows the
acting, which may trail the truth by a few seconds; the sidebar and the story strip show what the services reported
the moment it arrived. F1 opens a debug overlay with the raw events, trace IDs and feed cursors.
"""

import json

import pygame

from viewer import ui
from viewer.assets import Animator
from viewer.dialogue import customer_name, first_name
from viewer.scene import SIZE, SceneView
from viewer.updates import SERVICES

TOP = 44
STAGE = pygame.Rect(0, TOP, 1024, SIZE[1] - TOP)
SIDEBAR = pygame.Rect(1024, TOP, SIZE[0] - 1024, SIZE[1] - TOP)
CARD_X, CARD_W = SIDEBAR.x + 8, SIDEBAR.width - 16
STAGE_FLOOR = "#2f2933"

STATUS_COLOR = {"LIVE": ui.GOOD, "SYNCING": ui.WARN, "DISCONNECTED": ui.BAD, "RUN_MISMATCH": ui.BAD}
SERVICE_LABEL = {"order": "ORDER", "kitchen": "KITCHEN", "inventory": "STOCK"}
STAFF_SERVICE = {"cashier": "order", "chef": "kitchen", "stock_worker": "inventory"}
MOOD = {"CALM": ("ui_mood_calm", ui.GOOD_INK), "TENSE": ("ui_mood_busy", ui.WARN_INK),
        "SUSPICIOUS": ("ui_mood_stressed", ui.WARN_INK), "FURIOUS": ("ui_mood_stressed", ui.BAD_INK)}
EMOTE_ICON = {"warning": "ui_warning", "angry": "ui_mood_stressed", "unknown": "ui_mood_busy"}
TONE = {"good": ui.GOOD_INK, "bad": ui.BAD_INK, "warn": ui.WARN_INK, "info": ui.INK_SOFT}
STAMP = {"NEW": ui.INK_SOFT, "QUEUED": ui.INK_SOFT, "OVEN": ui.WARN_INK, "CHECK": ui.WARN_INK,
         "READY": ui.GOOD_INK, "FAILED": ui.BAD_INK, "???": ui.WARN_INK}
INGREDIENTS = ("DOUGH", "SAUCE", "CHEESE", "PEPPERONI", "BASIL")
SHORT_WHY = {"INVENTORY_UNAVAILABLE": "stockroom closed", "KITCHEN_UNAVAILABLE": "kitchen offline",
             "KITCHEN_QUEUE_FULL": "kitchen swamped", "QUEUE_FULL": "kitchen swamped",
             "COOK_DEADLINE_EXCEEDED": "waited too long", "FULFILLMENT_UNCONFIRMED": "unconfirmed",
             "DEPENDENCY_STATE_LOST": "kitchen restarted", "UNKNOWN_PIZZA": "not on the menu"}
FINISHED = {"READY", "FAILED"}
STORY_S = 9.0
MAX_BUBBLES = 4
TICKETS = 7


def wrap(text, width=28):
    """Character-count wrapping, kept for callers that do not have a font at hand."""
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


def clock(seconds):
    """A cosmetic shift clock: the doors open at 17:00 and a real second is a minute."""
    minutes = 17 * 60 + int(seconds)
    return f"Fri {minutes // 60 % 24:02d}:{minutes % 60:02d}"


class Renderer:
    def __init__(self, pack, scene_path=None):
        self.pack = pack
        self.scene = json.loads((scene_path or pack.root / "scene.json").read_text())
        self.view = SceneView(pack, self.scene, viewport=STAGE)
        self.origin = self.view.origin
        self.fonts = ui.Fonts()
        self.kit = ui.Kit(pack, self.fonts)
        self.oven = self.view.oven
        self.animators = {}
        self.debug = False
        self.cook_started = {}
        self.cook_seconds = 10.0
        self.pantry_full = {}

    def text(self, target, content, point, style="body", color=ui.CREAM, align="left"):
        return self.fonts.draw(target, content, point, style, color, align)

    # ------------------------------------------------------------------ animation clocks

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
        self._time_cooking(world)

    def _time_cooking(self, world):
        """Learn how long a bake takes from what the kitchen reports, for the tickets' progress bars."""
        for order_id, job in world.jobs.items():
            if job["status"] == "COOKING" and order_id not in self.cook_started:
                self.cook_started[order_id] = world.time
        for order_id in list(self.cook_started):
            status = (world.jobs.get(order_id) or {}).get("status")
            if status == "COOKING":
                continue
            took = world.time - self.cook_started.pop(order_id)
            if status == "COOKED" and took > 0.5:
                self.cook_seconds = 0.7 * self.cook_seconds + 0.3 * took

    # ------------------------------------------------------------------ frame

    def draw(self, target, world, *, mode, paused, speed, caption):
        target.fill(ui.NIGHT)
        pygame.draw.rect(target, STAGE_FLOOR, STAGE)
        self._scene(target, world)
        self._bubbles(target, world)
        self._story(target, world)
        self._sidebar(target, world)
        self._topbar(target, world, mode, paused, speed, caption)
        if self._opening(world):
            self._opening_card(target, world)
        if self.debug:
            self._debug(target, world)

    def _offline(self, world, actor):
        service = STAFF_SERVICE.get(actor.character)
        return service is not None and world.services[service].state in ("DISCONNECTED", "RUN_MISMATCH")

    def _scene(self, target, world):
        records = []
        for actor in world.everyone():
            if actor.gone:
                continue
            animator = self.animators.get(id(actor))
            records.append({"position": actor.position, "sprite": animator.frame_id if animator else f"{actor.character}_idle_se_00",
                            "carrying": actor.carrying if actor.action != "carry" else None, "alpha": actor.alpha,
                            "grey": self._offline(world, actor)})
        for prop in world.stage.props.values():
            records.append({"position": prop.position, "sprite": prop.sprite, "elevation": prop.elevation,
                            "depth_bias": prop.depth_bias, "alpha": prop.alpha})
        self.view.draw(target, records, oven_lit=any(job["status"] == "COOKING" for job in world.jobs.values()))
        for actor in world.everyone():
            if actor.gone or actor.alpha < 0.5:
                continue
            icon = "ui_pause" if self._offline(world, actor) else EMOTE_ICON.get(actor.emote) if actor.emote else None
            if icon:
                x, y = self.view.point(actor.position, 70)
                bob = 2 if int(world.time * 3) % 2 else 0
                self.kit.frame(target, (x - 11, y - 11 - bob, 22, 22), "ui_tooltip", shadow=True)
                self.kit.draw_icon(target, icon, (x, y - bob))

    # ------------------------------------------------------------------ speech

    def _bubbles(self, target, world):
        now = world.time
        recent = sorted((actor for actor in world.everyone() if not actor.gone and actor.speech and actor.alpha > 0.3),
                        key=lambda actor: actor.speech_since, reverse=True)[:MAX_BUBBLES]
        speakers = sorted(recent, key=lambda actor: actor.speech_since)  # older bubbles keep their place
        line_height = self.fonts.line_height("small")
        requests, details = [], []
        for actor in speakers:
            lines = self.fonts.wrap(actor.speech, 172, "small", 3)
            width = max(self.fonts.size(line, "small")[0] for line in lines) + 14
            height = line_height * len(lines) + 8
            feet_x, feet_y = self.view.point(actor.position)
            head_y = feet_y - 50 * self.view.scale
            requests.append((round(feet_x - width / 2), round(head_y - 9 - height), width, height))
            details.append((actor, lines, (round(feet_x), round(head_y))))
        for (actor, lines, anchor), (x, y, width, height) in zip(details, ui.layout_bubbles(requests, STAGE.inflate(-8, -8))):
            appear = min(1.0, max(0.0, (now - actor.speech_since) / 0.15))
            vanish = min(1.0, max(0.0, (actor.speech_until - now) / 0.25))
            self._bubble(target, pygame.Rect(x, y + round(3 * (1 - appear)), width, height), lines, anchor,
                         round(255 * min(appear, vanish)), line_height)

    def _bubble(self, target, rect, lines, anchor, alpha, line_height):
        if alpha <= 0:
            return
        layer = pygame.Surface((rect.width + 4, rect.height + 4), pygame.SRCALPHA)
        local = pygame.Rect(0, 0, rect.width, rect.height)
        self.kit.frame(layer, local, "ui_speech_bubble", shadow=True)
        for index, line in enumerate(lines):
            self.text(layer, line, (7, 4 + index * line_height), "small", ui.INK)
        if alpha < 255:
            layer.set_alpha(alpha)
        target.blit(layer, rect.topleft)
        # The tail points at the speaker; a bubble pushed upwards grows a thin stem.
        tail_x = max(rect.left + 8, min(anchor[0], rect.right - 8))
        base = rect.bottom - 2
        tip = (tail_x, min(base + 6, anchor[1]))
        if alpha > 128:
            if anchor[1] - tip[1] > 4:
                pygame.draw.line(target, ui.INK, (tail_x, tip[1]), (anchor[0], anchor[1] - 2), 1)
            pygame.draw.polygon(target, ui.PAPER, [(tail_x - 4, base), (tail_x + 4, base), tip])
            pygame.draw.line(target, ui.INK, (tail_x - 4, base + 1), tip, 1)
            pygame.draw.line(target, ui.INK, (tail_x + 4, base + 1), tip, 1)

    # ------------------------------------------------------------------ story strip

    def _story(self, target, world):
        stories = [(when, story) for when, story in world.story if world.time - when < STORY_S][-3:]
        bottom = STAGE.bottom - 12
        for when, story in reversed(stories):
            age = world.time - when
            alpha = round(255 * max(0.0, min(1.0, age / 0.25, (STORY_S - age) / 0.8)))
            text = self.fonts.render(story.text, "body", ui.INK)
            icon = self.kit.icon(story.icon)
            height = max(28, icon.get_height() + 8)
            rect = pygame.Rect(12, bottom - height, text.get_width() + icon.get_width() + 30, height)
            layer = pygame.Surface((rect.width + 4, rect.height + 4), pygame.SRCALPHA)
            self.kit.frame(layer, (0, 0, rect.width, rect.height), "ui_tooltip", shadow=True)
            pygame.draw.rect(layer, TONE.get(story.tone, ui.INK_SOFT), (3, 3, 3, rect.height - 6))
            layer.blit(icon, icon.get_rect(center=(12 + icon.get_width() // 2, rect.height // 2)))
            layer.blit(text, (icon.get_width() + 18, (rect.height - text.get_height()) // 2))
            layer.set_alpha(alpha)
            target.blit(layer, rect.topleft)
            bottom = rect.top - 6

    # ------------------------------------------------------------------ sidebar

    def _sidebar(self, target, world):
        pygame.draw.rect(target, ui.NIGHT, SIDEBAR)
        pygame.draw.line(target, ui.NIGHT_LINE, SIDEBAR.topleft, SIDEBAR.bottomleft)
        y = self._chef_card(target, world, SIDEBAR.y + 8)
        y = self._pantry_card(target, world, y + 8)
        self._tickets_card(target, world, y + 8, SIDEBAR.bottom - 44)
        self._tally(target, world, SIDEBAR.bottom - 30)

    def _chef_card(self, target, world, top):
        rect = pygame.Rect(CARD_X, top, CARD_W, 108)
        self.kit.frame(target, rect)
        portrait = self.kit.portrait("chef", 2)
        frame = pygame.Rect(rect.x + 8, rect.y + 8, portrait.get_width() + 4, portrait.get_height() + 4)
        pygame.draw.rect(target, ui.PAPER_DIM, frame)
        pygame.draw.rect(target, ui.INK, frame, 1)
        target.blit(portrait, (frame.x + 2, frame.y + 2), special_flags=0)
        if self._offline(world, world.actors["chef"]):
            target.blit(pygame.transform.grayscale(portrait), (frame.x + 2, frame.y + 2))
        mood = world.mood or {}
        label = world.mood_label
        icon, color = MOOD.get(label, MOOD["CALM"])
        x = frame.right + 10
        self.text(target, "Chef", (x, rect.y + 7), "bold", ui.INK)
        self.kit.draw_icon(target, icon, (x + 52, rect.y + 16))
        self.text(target, label.title(), (x + 64, rect.y + 9), "body", color)
        bar_x, bar_w = x + 58, rect.right - x - 68
        trust, patience = mood.get("trust"), mood.get("patience")
        self.text(target, "trust", (x, rect.y + 31), "small", ui.INK_SOFT)
        self.kit.bar(target, (bar_x, rect.y + 35, bar_w, 9), (trust or 0) / 100,
                     ui.GOOD if (trust or 0) >= 50 else ui.WARN, markers=(0.5,))
        self.text(target, "patience", (x, rect.y + 49), "small", ui.INK_SOFT)
        self.kit.bar(target, (bar_x, rect.y + 53, bar_w, 9), (patience or 0) / 100,
                     ui.GOOD if (patience or 0) >= 60 else ui.WARN if (patience or 0) >= 25 else ui.BAD, markers=(0.25,))
        baking = sum(1 for job in world.jobs.values() if job["status"] == "COOKING")
        waiting = sum(1 for job in world.jobs.values() if job["status"] in ("QUEUED", "CHECKING"))
        self.text(target, f"{baking} baking · {waiting} waiting", (x, rect.y + 69), "small", ui.INK_SOFT)
        doubting = trust is not None and trust < 50
        note = "Counts the shelves himself" if doubting else "Trusts the stock sheet"
        self.text(target, note, (rect.x + 10, rect.y + 87), "small", ui.WARN_INK if doubting else ui.INK_SOFT)
        return rect.bottom

    def _pantry_card(self, target, world, top):
        rect = pygame.Rect(CARD_X, top, CARD_W, 34 + 30 * len(INGREDIENTS))
        self.kit.frame(target, rect)
        self.text(target, "Pantry", (rect.x + 10, rect.y + 7), "bold", ui.INK)
        badges = []
        faults = world.faults or {}
        if faults.get("failureMode") == "UNAVAILABLE":
            badges.append(("CLOSED", ui.BAD_INK))
        if faults.get("delayMs"):
            badges.append(("SLOW", ui.WARN_INK))
        if faults.get("staleStockEnabled"):
            badges.append(("OLD SHEET", ui.WARN_INK))
        x = rect.right - 8
        for word, color in badges:
            width = self.fonts.size(word, "small")[0] + 10
            pill = pygame.Rect(x - width, rect.y + 8, width, 16)
            pygame.draw.rect(target, color, pill, border_radius=3)
            self.text(target, word, (pill.x + 5, pill.y + 1), "small", ui.PAPER)
            x = pill.x - 4
        y = rect.y + 30
        for name in INGREDIENTS:
            quantity = (world.stock or {}).get(name)
            full = self.pantry_full[name] = max(self.pantry_full.get(name, 0), quantity or 0, 1)
            self.kit.draw_icon(target, ui.INGREDIENT_ICONS[name], (rect.x + 24, y + 13), max_height=26)
            self.text(target, name.title(), (rect.x + 44, y + 1), "small", ui.INK)
            if quantity is None:
                self.text(target, "–", (rect.right - 10, y), "body", ui.INK_SOFT, align="right")
            else:
                level = quantity / full
                color = ui.BAD if quantity <= 0 else ui.WARN if level < 0.25 else ui.GOOD
                suffix = "?" if world.stock_uncertain else ""
                self.text(target, f"{quantity}{suffix}", (rect.right - 10, y), "body",
                          ui.BAD_INK if quantity <= 0 else ui.INK, align="right")
                self.kit.bar(target, (rect.x + 44, y + 17, rect.width - 56, 8), level, color)
            y += 30
        return rect.bottom

    def _tickets_card(self, target, world, top, bottom):
        rect = pygame.Rect(CARD_X, top, CARD_W, bottom - top)
        self.kit.frame(target, rect)
        counts = world.counters
        self.text(target, "Tickets", (rect.x + 10, rect.y + 7), "bold", ui.INK)
        self.text(target, f"{counts['open']} open", (rect.right - 10, rect.y + 9), "small", ui.INK_SOFT, align="right")
        orders = sorted(world.orders.values(), key=lambda order: order["sequence"])
        open_orders = [order for order in orders if order["status"] not in FINISHED]
        done = [order for order in reversed(orders) if order["status"] in FINISHED]
        rows = (open_orders + done)[:max(0, (rect.height - 34) // 50)]
        if not rows:
            self.text(target, "No orders yet", (rect.centerx, rect.y + 44), "small", ui.INK_SOFT, align="center")
        y = rect.y + 30
        for order in rows:
            self._ticket(target, world, order, pygame.Rect(rect.x + 6, y, rect.width - 12, 46))
            y += 50

    def _ticket(self, target, world, order, rect):
        stamp = self._stamp(world, order)
        faded = stamp in ("READY", "FAILED")
        pygame.draw.rect(target, ui.PAPER_DIM if faded else ui.PAPER, rect)
        pygame.draw.rect(target, ui.INK_SOFT, rect, 1)
        customer = order.get("customer") or {}
        face = self.kit.portrait(customer.get("appearance", "customer_amber"))
        target.blit(face, (rect.x + 4, rect.y + (rect.height - face.get_height()) // 2))
        x = rect.x + 38
        self.text(target, customer_name(customer) or "Guest", (x, rect.y + 3), "body", ui.INK)
        pizza = ui.PIZZA_ICONS.get(order.get("pizzaType"), "pizza_cheese")
        icon = self.kit.icon(pizza)
        target.blit(icon, (x, rect.y + 23))
        self.text(target, f"×{order.get('quantity', 1)}", (x + icon.get_width() + 3, rect.y + 25), "small", ui.INK_SOFT)
        width = self.fonts.size(stamp, "small")[0] + 10
        pill = pygame.Rect(rect.right - width - 4, rect.y + 4, width, 16)
        pygame.draw.rect(target, STAMP[stamp], pill, border_radius=3)
        self.text(target, stamp, (pill.x + 5, pill.y + 1), "small", ui.PAPER)
        detail_x = x + icon.get_width() + 26
        if stamp == "OVEN":
            started = self.cook_started.get(order["orderId"], world.time)
            progress = min(0.97, (world.time - started) / max(1.0, self.cook_seconds))
            self.kit.bar(target, (detail_x, rect.y + 27, rect.right - detail_x - 6, 8), progress, ui.WARN)
            return
        if stamp == "FAILED":
            reason = order.get("failureReason")
            if reason == "INSUFFICIENT_STOCK":
                job_error = ((order.get("lastError") or {}).get("details") or {}).get("jobError") or {}
                words = f"out of {first_name((job_error.get('details') or {}).get('missing'))}"
            else:
                words = SHORT_WHY.get(reason, "didn't work out")
            detail, color = words, ui.BAD_INK
        elif stamp == "READY":
            ticket = world.stage.tickets.get(order["orderId"])
            box = ticket.box if ticket is not None else "taken"  # the stage forgets tickets long since collected
            detail, color = {"counter": "on the counter", "taken": "picked up"}.get(box, "coming out"), ui.GOOD_INK
        else:
            detail, color = {"NEW": "at the register", "QUEUED": "in line", "CHECK": "double-checking",
                             "???": "unconfirmed"}.get(stamp, ""), ui.INK_SOFT
        lines = self.fonts.wrap(detail, rect.right - detail_x - 6, "small", 1)
        if lines:
            self.text(target, lines[0], (detail_x, rect.y + 25), "small", color)

    @staticmethod
    def _stamp(world, order):
        status = order["status"]
        if status in ("READY", "FAILED"):
            return status
        if status == "UNKNOWN":
            return "???"
        if status == "RECONCILING":
            return "CHECK"
        job = world.jobs.get(order["orderId"]) or {}
        if job.get("status") == "COOKING":
            return "OVEN"
        if job.get("status") in ("QUEUED", "CHECKING"):
            return "QUEUED"
        return "NEW"

    def _tally(self, target, world, y):
        counts = world.counters
        x = CARD_X + 4
        for icon, value, label in (("ui_heart", counts["ready"], "served"), ("ui_warning", counts["failed"], "failed"),
                                   ("ui_mood_busy", counts["unknown"], "unsure")):
            self.kit.draw_icon(target, icon, (x + 8, y + 9))
            drawn = self.text(target, f"{value} {label}", (x + 18, y + 3), "small", ui.CREAM)
            x = drawn.right + 8

    # ------------------------------------------------------------------ top bar

    def _topbar(self, target, world, mode, paused, speed, caption):
        pygame.draw.rect(target, ui.NIGHT_DEEP, (0, 0, SIZE[0], TOP))
        pygame.draw.line(target, ui.NIGHT_LINE, (0, TOP - 1), (SIZE[0], TOP - 1))
        self.kit.draw_icon(target, "pizza_pepperoni", (28, TOP // 2))
        self.text(target, "Little Slice", (50, 6), "bold", ui.CREAM)
        self.text(target, "PIZZAOPS", (50, 24), "small", ui.MUTED)
        badge = "LIVE" if mode == "live" else "REPLAY" if mode == "replay" else mode.upper()
        self.text(target, clock(world.time), (STAGE.centerx, 8), "bold", ui.CREAM, align="center")
        badge_width = self.fonts.size(badge, "small")[0] + 12
        pill = pygame.Rect(STAGE.centerx - badge_width // 2, 26, badge_width, 14)
        pygame.draw.rect(target, ui.BAD if badge == "LIVE" else ui.NIGHT_LINE, pill, border_radius=3)
        self.text(target, badge, (pill.centerx, pill.y), "small", ui.PAPER, align="center")
        x = 724
        for name in SERVICES:
            view = world.services[name]
            self.kit.lamp(target, (x + 5, 21), STATUS_COLOR.get(view.state, ui.MUTED))
            drawn = self.text(target, SERVICE_LABEL[name], (x + 14, 14), "small", ui.CREAM if view.state == "LIVE" else ui.MUTED)
            x = drawn.right + 16
        self.kit.draw_icon(target, "ui_pause" if paused else "ui_play", (SIZE[0] - 66, 21))
        self.text(target, "paused" if paused else f"{speed:g}×", (SIZE[0] - 54, 14), "small", ui.WARN if paused else ui.CREAM)
        self.text(target, "F1", (SIZE[0] - 12, 14), "small", ui.MUTED if not self.debug else ui.CREAM, align="right")

    # ------------------------------------------------------------------ overlays

    @staticmethod
    def _opening(world):
        states = [view.state for view in world.services.values()]
        return all(state != "LIVE" for state in states) or (world.time < 12 and any(state != "LIVE" for state in states))

    def _opening_card(self, target, world):
        rect = pygame.Rect(0, 0, 360, 124)
        rect.center = STAGE.center
        self.kit.frame(target, rect)
        self.text(target, "Opening the restaurant…", (rect.centerx, rect.y + 16), "bold", ui.INK, align="center")
        x = rect.x + 40
        for name in SERVICES:
            view = world.services[name]
            self.kit.lamp(target, (x, rect.y + 60), STATUS_COLOR.get(view.state, ui.MUTED), 5)
            self.text(target, SERVICE_LABEL[name].title(), (x + 10, rect.y + 52), "body", ui.INK)
            x += 100
        waiting = [SERVICE_LABEL[name].lower() for name in SERVICES if world.services[name].state != "LIVE"]
        hint = f"waiting for: {', '.join(waiting)}" if waiting else "all doors open"
        self.text(target, hint, (rect.centerx, rect.y + 88), "small", ui.INK_SOFT, align="center")

    def _debug(self, target, world):
        panel = STAGE.inflate(-40, -40)
        shade = pygame.Surface(panel.size, pygame.SRCALPHA)
        shade.fill((20, 16, 22, 225))
        target.blit(shade, panel)
        x, y = panel.x + 16, panel.y + 12
        self.text(target, "DEBUG  ·  raw service events (F1 to close)", (x, y), "bold", ui.CREAM)
        y += 28
        for name in SERVICES:
            view = world.services[name]
            instance = (view.instance_id or "-")[:8]
            self.text(target, f"{name:<10} {view.state:<13} instance {instance}  seq {view.last_sequence}",
                      (x, y), "small", STATUS_COLOR.get(view.state, ui.MUTED))
            y += 17
        faults = world.faults or {}
        self.text(target, f"stock v{world.stock_version}{' (uncertain)' if world.stock_uncertain else ''}   faults "
                          f"stale={faults.get('staleStockEnabled')} delay={faults.get('delayMs')} mode={faults.get('failureMode')}"
                          f"   resyncs {world.resyncs}", (x, y), "small", ui.MUTED)
        y += 26
        for line in list(world.ticker):
            who = (line["service"] or "")[:3].upper()
            trace = (line.get("traceId") or "")[:8]
            self.text(target, f"{who:<4}{line['type']:<24}{trace:<10}{line['detail']}"[:118], (x, y), "small", ui.CREAM)
            y += 17
