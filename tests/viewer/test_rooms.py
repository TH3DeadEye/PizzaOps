import json
import os
from itertools import pairwise

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame
import pytest

from viewer.assets import AssetPack, DEFAULT_ASSET_ROOT
from viewer.asset_demo import Showcase
from viewer.layout import Layout, ROOM_NAMES, load_scene
from viewer.scene import SIZE, STAGE
from viewer.world import World
from tests.viewer.harness import advance, event, order_record


def test_room_modules_and_composition_have_valid_registered_assets():
    manifest = json.loads((DEFAULT_ASSET_ROOT / "manifest.json").read_text())
    for name in ROOM_NAMES:
        scene = load_scene(name=name)
        layout = Layout(scene)
        assert len(layout.cells) == scene["size"][0] * scene["size"][1]
        assert layout.blocked <= layout.cells
        for obj in scene["objects"]:
            assert obj["sprite"] in manifest["sprites"]
            assert all(0 <= value < bound for value, bound in zip(obj["position"], scene["size"], strict=True))
        for actor in scene["characters"]:
            assert tuple(actor["position"]) in layout.walkable
            assert f"{actor['character']}/{actor['action']}/{actor['direction']}" in manifest["animations"]
            for point in actor.get("route", []):
                assert tuple(point) in layout.walkable
    full = load_scene()
    assert {room["id"] for room in full["rooms"]} == {"kitchen", "stockroom", "pickup", "dining"}


def test_destinations_are_reachable_without_crossing_walls_or_furniture():
    world = World()
    destinations = [world.oven_spot, world.pass_spot, world.crates_spot, world.overflow,
                    *world.wait_slots, *world.table_seats, *(point for point, _ in world.homes.values())]
    for goal in destinations:
        path = [world.entrance, *world.layout.route(world.entrance, goal)]
        assert path[-1] == goal
        assert set(path) <= world.layout.walkable
        assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in pairwise(path))
    # Stockroom access must pass through the opening in the kitchen partition.
    path = world.layout.route(world.homes["chef"][0], world.homes["stock_worker"][0])
    assert any(x == 8 and y in (3, 4) for x, y in path)
    with pytest.raises(ValueError, match="not walkable"):
        world.layout.route(world.entrance, (1, 1))  # oven footprint


def test_seated_customer_reaches_dining_and_returns_for_pickup():
    world = World()
    identifier = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    record = order_record(identifier)
    world.apply(event("order", "ORDER_RECEIVED", 1, record, identifier))
    world.apply(event("order", "ORDER_UNKNOWN", 2, {**record, "status": "UNKNOWN", "version": 2}, identifier))
    customer = world.customers[identifier]
    for _ in range(400):
        world.tick(0.05)
        cell = tuple(round(axis) for axis in customer.position)
        assert cell in world.layout.walkable
    assert customer.position == world.table_seats[1]
    world.apply(event("order", "ORDER_READY", 3, {**record, "status": "READY", "version": 3}, identifier))
    advance(world, 30)
    assert identifier not in world.customers


def test_all_rooms_render_and_switch_with_individual_pngs_and_atlases():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    for atlas in (False, True):
        demo = Showcase(AssetPack(use_atlas=atlas))
        for name in ROOM_NAMES:
            demo.select_room(name)
            demo.update(750)
            canvas = pygame.Surface(SIZE)
            demo.draw(canvas)
            assert demo.view.scale in (1, 2)
            assert demo.view.viewport == STAGE
            assert canvas.get_bounding_rect().size == SIZE
