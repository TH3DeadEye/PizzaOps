import os
import random
from itertools import combinations

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
import pytest

from viewer import ui
from viewer.assets import AssetPack


@pytest.fixture
def pack():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    return AssetPack()


def test_nine_slice_stretches_the_middle_and_keeps_the_corners():
    source = pygame.Surface((9, 9), pygame.SRCALPHA)
    source.fill((200, 200, 200, 255))
    corners = {(0, 0): (255, 0, 0, 255), (8, 0): (0, 255, 0, 255), (0, 8): (0, 0, 255, 255), (8, 8): (9, 9, 9, 255)}
    for point, color in corners.items():
        source.set_at(point, color)
    out = ui.nine_slice(source, (30, 20))
    assert out.get_size() == (30, 20)
    assert out.get_at((0, 0)) == corners[(0, 0)] and out.get_at((29, 0)) == corners[(8, 0)]
    assert out.get_at((0, 19)) == corners[(0, 8)] and out.get_at((29, 19)) == corners[(8, 8)]
    assert out.get_at((15, 10)) == (200, 200, 200, 255)


def test_bubbles_never_overlap_and_stay_on_stage():
    stage = pygame.Rect(8, 52, 1008, 780)
    rng = random.Random(4)
    for _ in range(200):
        x, y = rng.randint(0, 1000), rng.randint(0, 760)  # a crowd talking at once, around one spot
        requests = [(x + rng.randint(-30, 30), y + rng.randint(-20, 20), rng.randint(60, 186), rng.choice((21, 36, 51)))
                    for _ in range(4)]
        placed = [pygame.Rect(box) for box in ui.layout_bubbles(requests, stage)]
        assert all(stage.contains(rect) for rect in placed)
        assert not any(a.colliderect(b) for a, b in combinations(placed, 2))
    alone = ui.layout_bubbles([(100, 100, 80, 30)], stage)
    assert alone == [(100, 100, 80, 30)]  # an uncontested bubble stays exactly where it asked to be


def test_fonts_fall_back_when_the_pixel_font_is_missing(tmp_path):
    pygame.font.init()
    fallback = ui.Fonts(tmp_path / "missing.ttf")
    assert not fallback.pixel and fallback.render("hello").get_width() > 0
    assert fallback.wrap("one two three four five six", 40, max_lines=2)[-1].endswith("…")
    pixel = ui.Fonts()
    assert pixel.pixel == ui.FONT_PATH.exists()
    assert all(pixel.size(line)[0] <= 120 for line in pixel.wrap("Order up! One pepperoni for Robin, careful it's hot.", 120))


def test_icons_are_cropped_and_tall_ones_are_halved_crisply(pack):
    kit = ui.Kit(pack)
    assert kit.icon("ui_heart").get_size() == (13, 12)
    crate = kit.icon("crate_cheese", max_height=26)
    assert crate.get_height() <= 26 and crate.get_width() == kit.icon("crate_cheese").get_width() // 2
    assert kit.portrait("chef").get_size() == (28, 24) and kit.portrait("chef", 2).get_size() == (56, 48)
    canvas = pygame.Surface((300, 200), pygame.SRCALPHA)
    kit.frame(canvas, (10, 10, 120, 40), "ui_speech_bubble")
    kit.bar(canvas, (10, 80, 100, 9), 0.5, ui.GOOD, markers=(0.5,))
    assert canvas.get_at((12, 30))[3] == 255  # the frame was painted
