import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
import pytest

from tests.viewer.harness import event, order_record
from viewer.assets import AssetPack
from viewer.camera import Camera
from viewer.render import SIZE, STAGE, Renderer
from viewer.world import World

ROOM = pygame.Rect(-512, 100, 1020, 580)  # projected bounds that just fit the stage at 1×


def test_at_the_fitting_zoom_the_room_stays_centred():
    camera = Camera(STAGE, ROOM, 1)
    origin = camera.origin
    camera.pan(400, -300)
    camera.update(1.0)
    assert camera.origin == origin


def test_zoom_keeps_the_spot_under_the_cursor_and_the_view_stays_inside_the_room():
    camera = Camera(STAGE, ROOM, 1)
    cursor = (500, 430)
    before = camera.scene_point(cursor)
    camera.zoom_at(cursor, 2)
    assert camera.zoom == 2 and camera.scene_point(cursor) == pytest.approx(before, abs=1)
    for dx, dy in ((10_000, 10_000), (-10_000, -10_000)):
        camera.pan(dx, dy)
        camera.update(5.0)
        corner = camera.scene_point(STAGE.topleft)
        visible = pygame.Rect(round(corner[0]), round(corner[1]), STAGE.width // 2, STAGE.height // 2)
        assert ROOM.inflate(2, 2).contains(visible)
    camera.recenter()
    assert camera.zoom == 1 and camera.center == [ROOM.centerx, ROOM.centery]
    camera.zoom_at(cursor, 7)
    assert camera.zoom == 2  # integer zoom, capped: pixel art stays crisp


def test_the_view_glides_and_held_keys_pan():
    camera = Camera(STAGE, ROOM, 1)
    camera.toggle_zoom()
    start = camera.center[0]
    camera.pan(200, 0)
    camera.update(1 / 60)
    assert start < camera.center[0] < camera.target[0]
    for _ in range(120):
        camera.update(1 / 60)
    assert camera.center[0] == pytest.approx(camera.target[0], abs=0.5)
    target = camera.target[0]
    camera.update(0.25, (-1, 0))
    assert camera.target[0] < target


def test_the_restaurant_draws_zoomed_in_with_speech():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    renderer = Renderer(AssetPack())
    world = World()
    world.apply(event("order", "ORDER_RECEIVED", 1, order_record("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
                      "dddddddd-dddd-4ddd-8ddd-dddddddddddd"))
    renderer.update(world, 100)
    wide, close = pygame.Surface(SIZE), pygame.Surface(SIZE)
    renderer.draw(wide, world, mode="live", paused=False, speed=1.0, caption="LIVE")
    renderer.camera.toggle_zoom(STAGE.center)
    renderer.draw(close, world, mode="live", paused=False, speed=1.0, caption="LIVE")
    assert renderer.view.scale == 2 and renderer.camera.zoom == 2
    stage = pygame.Rect(STAGE)
    assert pygame.image.tobytes(wide.subsurface(stage), "RGB") != pygame.image.tobytes(close.subsurface(stage), "RGB")
