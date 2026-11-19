import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
import pytest

from tests.viewer.harness import event, order_record
from viewer.assets import AssetPack
from viewer.main import Show
from viewer.render import SIZE, Renderer, wrap
from viewer.world import World

ORDER = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


@pytest.fixture
def pack():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    return AssetPack()


def test_wrap_keeps_short_lines_and_caps_at_three():
    assert wrap("hello") == ["hello"]
    assert len(wrap("word " * 40)) == 3


def test_empty_world_and_a_speaking_customer_both_draw(pack):
    renderer = Renderer(pack)
    canvas = pygame.Surface(SIZE)
    world = World()
    renderer.draw(canvas, world, mode="live", paused=False, speed=1.0, caption="LIVE")
    world.apply(event("order", "ORDER_RECEIVED", 1, order_record(ORDER, appearance="customer_amber"), ORDER))
    renderer.update(world, 100)
    renderer.draw(canvas, world, mode="live", paused=False, speed=1.0, caption="LIVE")
    assert world.customers[ORDER].speech


def test_headless_live_loop_against_an_empty_feed(pack, tmp_path):
    class Empty:
        def drain(self):
            return []

        def stop(self):
            self.stopped = True

    show = Show(Empty(), pack=pack, caption="TEST")
    screenshot = tmp_path / "frame.png"
    from viewer.main import loop
    assert loop(show, mode="live", headless=True, frames=12, screenshot=screenshot, scale=1) == 0
    assert screenshot.exists() and screenshot.stat().st_size > 0
    assert getattr(show.source, "stopped", False)


def test_a_replay_waits_while_paused_and_follows_the_show_speed(pack, tmp_path):
    from viewer.main import Playback
    from viewer.feed import Replay
    from viewer.updates import Status, dumps
    recording = tmp_path / "shift.jsonl"
    recording.write_text("".join(dumps(Status("order", "LIVE", None, at)) + "\n" for at in (10.0, 11.0, 12.0)))
    playback = Playback()
    show = Show(Replay(recording, now=playback), pack=pack, caption="REPLAY", playback=playback)
    show.pump(16)
    delivered = len(show.world.ticker)
    show.paused = True
    for _ in range(100):
        show.pump(50)  # five seconds of paused wall time deliver nothing new
    assert len(show.world.ticker) == delivered and playback() == 0.016
    show.paused, show.speed = False, 4.0
    show.pump(250)  # a quarter second at 4x is one second of recording...
    show.pump(0)    # ...delivered on the next pump, which reads the clock first
    assert playback() == pytest.approx(1.016) and show.source._index == 2
