import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
import pytest

from viewer.assets import AssetPack
from viewer.render import SIZE, STAGE, Renderer
from viewer.world import World


@pytest.fixture
def renderer():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    return Renderer(AssetPack())


def test_cues_pop_sparkle_shake_and_raise_dust_then_fade_away(renderer):
    fx, world = renderer.fx, World()
    for name, position in (("take", (4, 11)), ("bell", (4, 10)), ("sting", (2, 3)), ("restock", (11, 4))):
        fx.cue(name, position)
    assert fx.pops and fx.sparkles and fx.shake > 0 and len(fx.particles) == 7
    assert fx.offset() != (0, 0)
    for _ in range(90):
        fx.update(1 / 30, world, oven_lit=False)
    assert not fx.pops and not fx.sparkles and not fx.particles and fx.offset() == (0, 0)


def test_only_a_lit_oven_smokes(renderer):
    world = World()
    for _ in range(30):
        renderer.fx.update(1 / 30, world, oven_lit=False)
    assert not renderer.fx.particles
    for _ in range(30):
        renderer.fx.update(1 / 30, world, oven_lit=True)
    assert renderer.fx.particles


def test_lighting_can_be_switched_off_without_touching_the_world(renderer):
    world = World()
    frames = []
    for lighting in (True, False):
        renderer.fx.lighting = lighting
        canvas = pygame.Surface(SIZE)
        renderer.draw(canvas, world, mode="live", paused=False, speed=1.0, caption="LIVE")
        frames.append(pygame.image.tobytes(canvas.subsurface(STAGE), "RGB"))
    assert frames[0] != frames[1]
    assert world.orders == {} and world.stage.props == {}
