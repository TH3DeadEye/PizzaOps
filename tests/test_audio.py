"""The game's sounds are built deterministically, match their build tool, and the viewer copes with or without audio."""

import os
import wave

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
import pytest

from tools import build_audio
from viewer.audio import AUDIO_DIR, Audio, CUE_SOUNDS

LOOPS = {"music": 40.0, "room_tone": 8.0, "oven_crackle": 4.0}


def test_every_sound_exists_loads_and_matches_its_build_recipe():
    pygame.mixer.init()
    try:
        for name in build_audio.SOUNDS:
            path = AUDIO_DIR / f"{name}.wav"
            assert path.exists(), f"run tools/build_audio.py: {path} is missing"
            length = pygame.mixer.Sound(str(path)).get_length()
            assert length >= LOOPS.get(name, 0.05) - 0.01
        for name in ("door_chime", "register", "order_bell", "sting"):  # short ones: rebuild and compare byte for byte
            with wave.open(str(AUDIO_DIR / f"{name}.wav")) as stored:
                assert stored.readframes(stored.getnframes()) == build_audio.render(name) == build_audio.render(name)
        assert {sound for sound, _ in CUE_SOUNDS.values()} <= set(build_audio.SOUNDS)
    finally:
        pygame.mixer.quit()


def test_disabled_audio_is_silent_and_harmless():
    audio = Audio(enabled=False)
    assert audio.state == "off"
    audio.cue("bell", 100)
    audio.update(True)
    audio.toggle_mute()
    audio.close()
    assert audio.state == "off" and not audio.pending


def test_cues_play_once_per_moment_wait_when_delayed_and_follow_the_oven():
    clock = {"t": 100.0}
    audio = Audio(now=lambda: clock["t"])
    try:
        assert audio.state == "on"
        audio.cue("bell", 900)
        first = audio.last["order_bell"]
        audio.cue("bell", 100)  # the same effect a split second later is skipped
        assert audio.last["order_bell"] == first
        audio.cue("register", 300, delay=1.0)
        audio.update(False)
        assert "register" not in audio.last
        clock["t"] += 1.1
        audio.update(True)
        assert "register" in audio.last and audio.oven_on
        audio.toggle_mute()
        assert audio.state == "muted"
        audio.cue("fail", 500)
        assert "fail" not in audio.last
        audio.update(False)
        assert not audio.oven_on
    finally:
        audio.close()
        pygame.mixer.quit()


@pytest.mark.parametrize("name", sorted(LOOPS))
def test_loops_join_without_a_jump(name):
    with wave.open(str(AUDIO_DIR / f"{name}.wav")) as stored:
        frames = stored.readframes(stored.getnframes())
    first = int.from_bytes(frames[:2], "little", signed=True)
    last = int.from_bytes(frames[-2:], "little", signed=True)
    assert abs(last - first) < 0.03 * 32767
