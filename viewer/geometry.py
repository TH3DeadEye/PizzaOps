"""Isometric projection and movement arithmetic shared by the showcase and the live world. No pygame here."""

import math

TILE_HALF_WIDTH = 32
TILE_HALF_HEIGHT = 16


def project(position, origin=(0, 0), elevation=0):
    return (round(origin[0] + (position[0] - position[1]) * TILE_HALF_WIDTH),
            round(origin[1] + (position[0] + position[1]) * TILE_HALF_HEIGHT - elevation))


def unproject(point, origin=(0, 0)):
    screen_x = (point[0] - origin[0]) / TILE_HALF_WIDTH
    screen_y = (point[1] - origin[1]) / TILE_HALF_HEIGHT
    return ((screen_x + screen_y) / 2, (screen_y - screen_x) / 2)


def depth_key(record):
    return (sum(record["position"]) + record.get("depth_bias", 0), record["position"][0])


def direction_for(delta):
    if abs(delta[0]) >= abs(delta[1]):
        return "se" if delta[0] >= 0 else "nw"
    return "sw" if delta[1] >= 0 else "ne"


def step_towards(position, destination, distance):
    """Move up to ``distance`` tiles from ``position`` towards ``destination``.

    Returns ``(new_position, direction, arrived)``; ``direction`` is None when there was nothing to move.
    """
    delta = (destination[0] - position[0], destination[1] - position[1])
    remaining = math.hypot(*delta)
    if remaining < 1e-9:
        return (destination[0], destination[1]), None, True
    if distance >= remaining:
        return (destination[0], destination[1]), direction_for(delta), True
    scale = distance / remaining
    return (position[0] + delta[0] * scale, position[1] + delta[1] * scale), direction_for(delta), False


def move_actor(actor, delta_seconds, speed=1.25):
    """Advance a showcase actor along its looping ``route`` (list of waypoints) in place."""
    if not actor.get("route"):
        return
    remaining = delta_seconds * speed
    while remaining > 0:
        destination = actor["route"][actor["route_index"]]
        delta = [destination[axis] - actor["position"][axis] for axis in (0, 1)]
        distance = math.hypot(*delta)
        if distance < 1e-6:
            actor["route_index"] = (actor["route_index"] + 1) % len(actor["route"])
            if all(point == actor["position"] for point in actor["route"]):
                break
            continue
        actor["direction"] = direction_for(delta)
        step = min(distance, remaining)
        actor["position"] = [actor["position"][axis] + delta[axis] / distance * step for axis in (0, 1)]
        remaining -= step
        if step == distance:
            actor["route_index"] = (actor["route_index"] + 1) % len(actor["route"])
