from __future__ import annotations

import hashlib
import heapq
from itertools import count
from typing import Any


SUBDIV = 4

NORTH = 1
EAST = 2
SOUTH = 4
WEST = 8

DIRECTIONS = (
    (0, 1, NORTH),
    (1, 0, EAST),
    (0, -1, SOUTH),
    (-1, 0, WEST),
)
DIRECTION_INDEX = {(di, dj): index for index, (di, dj, _) in enumerate(DIRECTIONS)}

# Rotation zero is the canonical orientation for each tile shape.
BASE_MASKS = {
    "straight": NORTH | SOUTH,
    "corner": SOUTH | WEST,
    "tee": WEST | EAST | SOUTH,
    "end": SOUTH,
    "cross": NORTH | EAST | SOUTH | WEST,
}

_TILE_BY_POPCOUNT = {
    1: "end",
    3: "tee",
    4: "cross",
}

Node = tuple[int, int]
Edge = tuple[Node, Node]


def rotate_mask(mask: int, turns: int) -> int:
    """Rotate a four-direction road mask clockwise by quarter turns."""
    turns %= 4
    for _ in range(turns):
        mask = ((mask << 1) & 0b1111) | (mask >> 3)
    return mask


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _canonical_edge(a: Node, b: Node) -> Edge:
    return (a, b) if a < b else (b, a)


def _edge_jitter(seed: int, a: Node, b: Node) -> float:
    first, second = _canonical_edge(a, b)
    digest = int(_stable_hash(f"{seed}:{first[0]},{first[1]}:{second[0]},{second[1]}"), 16)
    return (digest / (16**12)) * 0.1


def _location_data(
    locations: list[dict[str, Any]], grid_size: int
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[int, int]]]:
    by_id: dict[str, dict[str, Any]] = {}
    coordinates: dict[str, tuple[int, int]] = {}
    occupied: set[tuple[int, int]] = set()
    for location in locations:
        location_id = location["id"]
        if not isinstance(location_id, str):
            raise ValueError("Location ids must be strings")
        if location_id in by_id:
            raise ValueError(f"Duplicate location id: {location_id}")
        coordinate = location.get("coordinates", {})
        x, y = coordinate.get("x"), coordinate.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            raise ValueError(f"Location {location_id} must have integer cell coordinates")
        if not (0 <= x < grid_size and 0 <= y < grid_size):
            raise ValueError(f"Location {location_id} is outside the {grid_size}x{grid_size} grid")
        by_id[location_id] = location
        coordinates[location_id] = (x, y)
        occupied.add((x, y))
    if len(occupied) != len(locations):
        raise ValueError("There must be one location per cell")
    return by_id, coordinates


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _logical_links(
    location_ids: list[str], coordinates: dict[str, tuple[int, int]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(location_ids) < 2:
        return [], []

    parent = {location_id: location_id for location_id in location_ids}
    rank = {location_id: 0 for location_id in location_ids}

    def find(location_id: str) -> str:
        while parent[location_id] != location_id:
            parent[location_id] = parent[parent[location_id]]
            location_id = parent[location_id]
        return location_id

    def union(a: str, b: str) -> bool:
        root_a, root_b = find(a), find(b)
        if root_a == root_b:
            return False
        if rank[root_a] < rank[root_b]:
            root_a, root_b = root_b, root_a
        parent[root_b] = root_a
        if rank[root_a] == rank[root_b]:
            rank[root_a] += 1
        return True

    candidates = [
        ( _manhattan(coordinates[a], coordinates[b]), a, b)
        for index, a in enumerate(location_ids)
        for b in location_ids[index + 1 :]
    ]
    candidates.sort()

    tree: list[dict[str, Any]] = []
    tree_pairs: set[tuple[str, str]] = set()
    adjacency: dict[str, list[tuple[str, int]]] = {location_id: [] for location_id in location_ids}
    for distance, a, b in candidates:
        if not union(a, b):
            continue
        tree.append({"from": a, "to": b, "kind": "tree", "length": distance})
        tree_pairs.add((a, b))
        adjacency[a].append((b, distance))
        adjacency[b].append((a, distance))
        if len(tree) == len(location_ids) - 1:
            break

    degrees = {location_id: len(adjacency[location_id]) for location_id in location_ids}
    loop_limit = max(2, round(0.15 * len(location_ids)))
    loops: list[dict[str, Any]] = []

    for distance, a, b in candidates:
        if (a, b) in tree_pairs or degrees[a] >= 4 or degrees[b] >= 4:
            continue

        pending = [(a, 0)]
        visited = {a}
        tree_path_length = None
        while pending:
            current, path_length = pending.pop()
            if current == b:
                tree_path_length = path_length
                break
            for neighbor, edge_length in sorted(adjacency[current]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append((neighbor, path_length + edge_length))

        if tree_path_length is not None and tree_path_length >= 2.5 * distance:
            loops.append({"from": a, "to": b, "kind": "loop", "length": distance})
            degrees[a] += 1
            degrees[b] += 1
            if len(loops) >= loop_limit:
                break

    return tree, loops


def _gate(coordinates: tuple[int, int], subdiv: int) -> Node:
    x, y = coordinates
    return (x * subdiv + subdiv // 2, y * subdiv)


def _allowed_edge(a: Node, b: Node, subdiv: int) -> bool:
    if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
        return False
    if a[1] == b[1]:
        return a[1] % subdiv == 0
    return a[0] % subdiv == 0


def _neighbors(node: Node, lattice_size: int, subdiv: int):
    i, j = node
    for di, dj, _ in DIRECTIONS:
        neighbor = (i + di, j + dj)
        if not (0 <= neighbor[0] <= lattice_size and 0 <= neighbor[1] <= lattice_size):
            continue
        if _allowed_edge(node, neighbor, subdiv):
            yield neighbor


def _route(
    source: Node,
    target: Node,
    used_edges: set[Edge],
    seed: int,
    lattice_size: int,
    subdiv: int,
) -> list[Node]:
    serial = count()
    distances: dict[tuple[int, int, int], float] = {}
    predecessors: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    queue: list[tuple[float, int, tuple[int, int, int]]] = []
    jitter_cache: dict[Edge, float] = {}

    initial_state = (source[0], source[1], -1)
    distances[initial_state] = 0.0
    heapq.heappush(queue, (0.0, next(serial), initial_state))

    final_state = None
    while queue:
        cost, _, state = heapq.heappop(queue)
        if cost != distances.get(state):
            continue
        node = (state[0], state[1])
        if node == target:
            final_state = state
            break

        previous_direction = state[2]
        for neighbor in _neighbors(node, lattice_size, subdiv):
            direction = DIRECTION_INDEX[(neighbor[0] - node[0], neighbor[1] - node[1])]
            edge = _canonical_edge(node, neighbor)
            jitter = jitter_cache.get(edge)
            if jitter is None:
                jitter = _edge_jitter(seed, node, neighbor)
                jitter_cache[edge] = jitter
            step_cost = (0.25 if edge in used_edges else 1.0) + jitter
            if previous_direction != -1 and previous_direction != direction:
                step_cost += 0.4
            next_state = (neighbor[0], neighbor[1], direction)
            next_cost = cost + step_cost
            if next_cost >= distances.get(next_state, float("inf")):
                continue
            distances[next_state] = next_cost
            predecessors[next_state] = state
            heapq.heappush(queue, (next_cost, next(serial), next_state))

    if final_state is None:
        raise RuntimeError("Could not route a road between connected locations")

    path: list[Node] = []
    state = final_state
    while True:
        path.append((state[0], state[1]))
        if state not in predecessors:
            break
        state = predecessors[state]
    path.reverse()
    return path


def _path_edges(path: list[Node]) -> set[Edge]:
    return {_canonical_edge(a, b) for a, b in zip(path, path[1:])}


def _node_records(edges: set[Edge]) -> list[dict[str, Any]]:
    masks: dict[Node, int] = {}
    for a, b in sorted(edges):
        masks[a] = masks.get(a, 0)
        masks[b] = masks.get(b, 0)
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx == 1:
            masks[a] |= EAST
            masks[b] |= WEST
        elif dx == -1:
            masks[a] |= WEST
            masks[b] |= EAST
        elif dy == 1:
            masks[a] |= NORTH
            masks[b] |= SOUTH
        else:
            masks[a] |= SOUTH
            masks[b] |= NORTH

    nodes = []
    for (i, j), mask in sorted(masks.items(), key=lambda item: (item[0][1], item[0][0])):
        popcount = mask.bit_count()
        if popcount == 2:
            tile = "straight" if mask in (NORTH | SOUTH, EAST | WEST) else "corner"
        else:
            tile = _TILE_BY_POPCOUNT[popcount]
        rotation = next(
            turns for turns in range(4) if rotate_mask(BASE_MASKS[tile], turns) == mask
        )
        nodes.append({"i": i, "j": j, "mask": mask, "tile": tile, "rotation": rotation})
    return nodes


def _set_connections(locations: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
    neighbors = {location["id"]: set() for location in locations}
    for link in links:
        a, b = link["from"], link["to"]
        neighbors.setdefault(a, set()).add(b)
        neighbors.setdefault(b, set()).add(a)
    for location in locations:
        location["connections"] = sorted(neighbors.get(location["id"], set()))


def generate_road_network(
    locations: list[dict[str, Any]],
    seed: int,
    grid_size: int,
    subdiv: int = SUBDIV,
) -> dict[str, Any]:
    if grid_size < 1 or subdiv < 1:
        raise ValueError("grid_size and subdiv must be positive")
    if subdiv % 2:
        raise ValueError("subdiv must be even to place a centered south-side gate")

    by_id, coordinates = _location_data(locations, grid_size)
    location_ids = sorted(by_id)
    tree, loops = _logical_links(location_ids, coordinates)
    links: list[dict[str, Any]] = []
    used_edges: set[Edge] = set()
    lattice_size = grid_size * subdiv

    for kind, logical_edges in (("tree", tree), ("loop", loops)):
        for logical_edge in sorted(
            logical_edges,
            key=lambda edge: (edge["length"], edge["from"], edge["to"]),
        ):
            from_id, to_id = logical_edge["from"], logical_edge["to"]
            from_gate = _gate(coordinates[from_id], subdiv)
            to_gate = _gate(coordinates[to_id], subdiv)
            path = _route(
                from_gate,
                to_gate,
                used_edges,
                seed,
                lattice_size,
                subdiv,
            )
            used_edges.update(_path_edges(path))
            links.append({"from": from_id, "to": to_id, "kind": kind, "path": [[i, j] for i, j in path]})
    if len(location_ids) == 1:
        only_id = location_ids[0]
        gate = _gate(coordinates[only_id], subdiv)
        used_edges.add(_canonical_edge(gate, (gate[0] - 1, gate[1])))

    access = {
        location_id: {
            "side": "S",
            "node": list(_gate(coordinates[location_id], subdiv)),
        }
        for location_id in location_ids
    }

    _set_connections(locations, links)
    return {
        "version": 2,
        "subdiv": subdiv,
        "latticeSize": lattice_size,
        "nodes": _node_records(used_edges),
        "links": links,
        "access": access,
    }


def _edges_from_nodes(nodes: list[dict[str, Any]]) -> set[Edge]:
    masks = {(node["i"], node["j"]): node["mask"] for node in nodes}
    edges = set()
    for node, mask in masks.items():
        for di, dj, bit in DIRECTIONS:
            neighbor = (node[0] + di, node[1] + dj)
            if mask & bit and neighbor in masks:
                edges.add(_canonical_edge(node, neighbor))
    return edges


def attach_location(
    network: dict[str, Any],
    location: dict[str, Any],
    existing_locations: list[dict[str, Any]],
    seed: int,
    grid_size: int,
    subdiv: int = SUBDIV,
) -> dict[str, Any]:
    """Append a south-gate route without replacing existing links or road edges."""
    if grid_size < 1 or subdiv < 1:
        raise ValueError("grid_size and subdiv must be positive")
    if subdiv % 2:
        raise ValueError("subdiv must be even to place a centered south-side gate")
    location_id = location["id"]
    if any(existing["id"] == location_id for existing in existing_locations):
        raise ValueError(f"Location already exists: {location_id}")
    all_locations = [*existing_locations, location]
    _, coordinates = _location_data(all_locations, grid_size)
    if network.get("subdiv", subdiv) != subdiv or network.get("latticeSize", grid_size * subdiv) != grid_size * subdiv:
        raise ValueError("Network lattice settings do not match the requested grid")
    if network.get("version") != 2:
        raise ValueError("attach_location requires a version-2 road network")

    links = network.setdefault("links", [])
    edges = _edges_from_nodes(network.get("nodes", []))
    lattice_size = grid_size * subdiv
    if existing_locations:
        nearest_id = min(
            (existing["id"] for existing in existing_locations),
            key=lambda other_id: (_manhattan(coordinates[location_id], coordinates[other_id]), other_id),
        )
        from_id, to_id = location_id, nearest_id
        from_gate = _gate(coordinates[from_id], subdiv)
        to_gate = _gate(coordinates[to_id], subdiv)
        path = _route(
            from_gate,
            to_gate,
            edges,
            seed,
            lattice_size,
            subdiv,
        )
        edges.update(_path_edges(path))
        links.append({"from": from_id, "to": to_id, "kind": "tree", "path": [[i, j] for i, j in path]})
    else:
        gate = _gate(coordinates[location_id], subdiv)
        edges.add(_canonical_edge(gate, (gate[0] - 1, gate[1])))

    access = network.setdefault("access", {})
    access[location_id] = {"side": "S", "node": list(_gate(coordinates[location_id], subdiv))}
    network["version"] = 2
    network["subdiv"] = subdiv
    network["latticeSize"] = lattice_size
    network["nodes"] = _node_records(edges)
    _set_connections(all_locations, links)
    return network
