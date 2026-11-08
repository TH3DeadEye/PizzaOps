"""Room loading and tile routes through actual doorways and furniture gaps."""

import json
from collections import deque

from viewer.assets import DEFAULT_ASSET_ROOT

ROOM_NAMES = ("restaurant", "kitchen", "stockroom", "pickup", "dining", "patio", "lounge")


def load_scene(root=DEFAULT_ASSET_ROOT, name="restaurant"):
    if name not in ROOM_NAMES:
        raise ValueError(f"Unknown room: {name}")
    path = root / "scene.json" if name == "restaurant" else root / "rooms" / f"{name}.json"
    return json.loads(path.read_text())


class Layout:
    def __init__(self, scene=None):
        self.scene = scene if scene is not None else load_scene()
        self.landmarks = self.scene.get("landmarks", {})
        self.cells = {tuple(tile["position"]) for tile in self.scene["floors"]}
        self.blocked = set(map(tuple, self.scene.get("blocked", [])))
        self.walkable = self.cells - self.blocked

    def route(self, start, destination):
        """Four-neighbour paths never cut a diagonal through a wall or table.

        Actors may be between cells when an event interrupts them; return to the
        nearest walkable centre before joining the new route.
        """
        goal = tuple(destination)
        if goal not in self.walkable:
            raise ValueError(f"Destination is not walkable: {goal}")
        source = min(self.walkable, key=lambda p: (p[0] - start[0]) ** 2 + (p[1] - start[1]) ** 2)
        queue, previous = deque([source]), {source: None}
        while queue and goal not in previous:
            x, y = queue.popleft()
            for point in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):
                if point in self.walkable and point not in previous:
                    previous[point] = (x, y)
                    queue.append(point)
        if goal not in previous:
            raise ValueError(f"No doorway route from {start} to {destination}")
        path, point = [], goal
        while point is not None:
            path.append(point)
            point = previous[point]
        path.reverse()
        if tuple(start) == source:
            path = path[1:]
        return path
