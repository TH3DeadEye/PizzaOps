import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
import pytest

from tools.validate_assets import validate
from viewer.assets import Animator, AssetPack
from viewer.asset_demo import Showcase, depth_key, direction_for, move_actor, project, unproject


@pytest.fixture
def pack():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    return AssetPack()


def test_entire_asset_pack():
    assert validate()["status"] == "passed"


def test_animation_timing_and_switch(pack):
    animator = Animator(pack, "chef/walk/se")
    frames = pack.animations[animator.clip]["frames"]
    assert animator.frame_id == frames[0]
    animator.update(140)
    assert animator.frame_id == frames[1]
    animator.update(420 + 280)
    assert animator.frame_id == frames[2]
    animator.play("chef/work/se")
    assert animator.elapsed_ms == 0
    animator.update(50)
    animator.play("chef/work/se")
    assert animator.elapsed_ms == 50
    with pytest.raises(ValueError):
        animator.update(-1)
    with pytest.raises(KeyError):
        animator.play("not/a/clip")


@pytest.mark.parametrize("position", [(0, 0), (3, 4), (-2, 5), (4.5, 7.25)])
def test_projection_round_trip(position):
    recovered = unproject(project(position, (120, 140)), (120, 140))
    assert recovered == pytest.approx(position)


def test_loader_atlas_matches_and_scales(pack):
    atlas_pack = AssetPack(use_atlas=True)
    for sprite_id in ("oven_off", "chef_walk_ne_02", "ui_panel", "floor_checker"):
        image = pack.image(sprite_id)
        assert pygame.image.tobytes(image, "RGBA") == pygame.image.tobytes(atlas_pack.image(sprite_id), "RGBA")
        scaled = pack.image(sprite_id, 2)
        assert scaled.get_size() == (image.get_width() * 2, image.get_height() * 2)
        for point in ((0, 0), (image.get_width() // 2, image.get_height() // 2)):
            assert image.get_at(point) == scaled.get_at((point[0] * 2, point[1] * 2))
    with pytest.raises(ValueError):
        pack.image("oven_off", 1.5)


def test_route_crosses_corners_without_overshoot():
    actor = {"position": [0, 0], "route": [[0, 0], [1, 0], [1, 1]], "route_index": 1}
    move_actor(actor, 1.2)
    assert actor["position"] == pytest.approx([1, 0.5])
    assert actor["direction"] == "sw"
    assert [direction_for(delta) for delta in ([1, 0], [-1, 0], [0, 1], [0, -1])] == ["se", "nw", "sw", "ne"]


def test_depth_uses_ground_position_not_elevation():
    objects = [{"position": [2, 3], "elevation": 100}, {"position": [3, 3]}, {"position": [2, 3], "depth_bias": 0.01}]
    assert sorted(objects, key=depth_key) == [objects[0], objects[2], objects[1]]


def test_showcase_all_actions_and_directions(pack):
    demo = Showcase(pack)
    canvas = pygame.Surface((900, 600))
    demo.draw(canvas)
    for action_index in range(6):
        for direction in ("se", "sw", "ne", "nw"):
            demo.gallery = True
            demo.gallery_action = action_index
            demo.gallery_direction = direction
            demo.update(500)
            demo.draw(canvas)
    demo.paused = True
    elapsed = demo.elapsed
    demo.update(500)
    assert demo.elapsed == elapsed
