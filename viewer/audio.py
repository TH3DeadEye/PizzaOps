"""Sound for the live viewer: effects on stage cues, a crackling oven while it bakes, room tone and a music loop.

Audio is optional. Headless runs, machines without a sound device, ``--no-audio`` and a missing audio folder all
fall back to silence, and nothing else in the viewer depends on it. Effects pan with where they happen on stage and
the same effect never stacks within a split second. The sounds themselves are built by ``tools/build_audio.py``.
"""

import time
from pathlib import Path

import pygame

AUDIO_DIR = Path(__file__).resolve().parents[1] / "assets" / "game" / "audio"
CUE_SOUNDS = {  # stage cue: (sound, volume)
    "door": ("door_chime", 0.5), "register": ("register", 0.45), "ticket": ("ticket", 0.3),
    "oven": ("oven_whoosh", 0.4), "bell": ("order_bell", 0.55), "take": ("click", 0.45),
    "fail": ("fail", 0.5), "restock": ("restock", 0.55), "sting": ("sting", 0.65), "bin": ("ticket", 0.25),
}
COOLDOWN_S = 0.12
MUSIC_VOLUME, ROOM_VOLUME, OVEN_VOLUME = 0.3, 0.35, 0.45


class Audio:
    def __init__(self, enabled=True, folder=AUDIO_DIR, now=time.monotonic, width=1024):
        self.enabled, self.muted, self.oven_on = False, False, False
        self.now, self.width = now, width
        self.sounds, self.last, self.pending = {}, {}, []
        self.room = self.oven = None
        folder = Path(folder)
        if not enabled or not folder.exists():
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            pygame.mixer.set_num_channels(16)
            pygame.mixer.set_reserved(2)  # 0: room tone, 1: oven crackle
            self.sounds = {path.stem: pygame.mixer.Sound(str(path)) for path in folder.glob("*.wav") if path.stem != "music"}
            self.room, self.oven = pygame.mixer.Channel(0), pygame.mixer.Channel(1)
            if "room_tone" in self.sounds:
                self.room.play(self.sounds["room_tone"], loops=-1, fade_ms=1500)
                self.room.set_volume(ROOM_VOLUME)
            if (folder / "music.wav").exists():
                pygame.mixer.music.load(str(folder / "music.wav"))
                pygame.mixer.music.set_volume(MUSIC_VOLUME)
                pygame.mixer.music.play(loops=-1, fade_ms=2500)
        except pygame.error:
            self.sounds = {}
            return
        self.enabled = True

    @property
    def state(self):
        return "off" if not self.enabled else "muted" if self.muted else "on"

    def cue(self, name, screen_x=None, delay=0.0):
        """Play the effect for a stage cue, panned by its x on screen; ``delay`` seconds later if asked."""
        if not self.enabled:
            return
        if delay > 0:
            self.pending.append((self.now() + delay, name, screen_x))
            return
        sound, volume = CUE_SOUNDS.get(name, (None, 0.0))
        if self.muted or sound not in self.sounds:
            return
        now = self.now()
        if now - self.last.get(sound, float("-inf")) < COOLDOWN_S:
            return
        self.last[sound] = now
        channel = self.sounds[sound].play()
        if channel is not None:
            pan = 0.5 if screen_x is None else max(0.0, min(1.0, screen_x / self.width))
            channel.set_volume(volume * (1.0 - 0.6 * pan), volume * (0.4 + 0.6 * pan))

    def update(self, oven_lit):
        """Fire delayed cues and keep the oven's crackle in step with whether anything is baking."""
        if not self.enabled:
            return
        now = self.now()
        due = [item for item in self.pending if item[0] <= now]
        self.pending = [item for item in self.pending if item[0] > now]
        for _, name, screen_x in due:
            self.cue(name, screen_x)
        if oven_lit != self.oven_on and "oven_crackle" in self.sounds:
            self.oven_on = oven_lit
            if oven_lit:
                self.oven.play(self.sounds["oven_crackle"], loops=-1, fade_ms=600)
                self.oven.set_volume(0.0 if self.muted else OVEN_VOLUME)
            else:
                self.oven.fadeout(900)

    def toggle_mute(self):
        if not self.enabled:
            return
        self.muted = not self.muted
        level = 0.0 if self.muted else 1.0
        pygame.mixer.music.set_volume(MUSIC_VOLUME * level)
        self.room.set_volume(ROOM_VOLUME * level)
        self.oven.set_volume(OVEN_VOLUME * level)

    def close(self):
        if self.enabled:
            pygame.mixer.music.stop()
            pygame.mixer.stop()
            self.enabled = False
