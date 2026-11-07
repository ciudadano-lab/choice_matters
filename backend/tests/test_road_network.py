import copy
import json
import time

import pytest

from road_network import (
    BASE_MASKS,
    EAST,
    NORTH,
    SOUTH,
    SUBDIV,
    WEST,
    attach_location,
    generate_road_network,
    rotate_mask,
)


def _locations(coordinates):
    return [
        {
            "id": f"location_{index:02}",
            "coordinates": {"x": x, "y": y},
            "connections": [],
        }
        for index, (x, y) in enumerate(coordinates)
    ]


def _network_edges(network):
    edges = set()
    nodes = {(node["i"], node["j"]): node["mask"] for node in network["nodes"]}
    direction_bits = (
        (0, 1, NORTH),
        (1, 0, EAST),
        (0, -1, SOUTH),
        (-1, 0, WEST),
    )
    for (i, j), mask in nodes.items():
        for di, dj, bit in direction_bits:
            neighbor = (i + di, j + dj)
            if mask & bit:
                assert neighbor in nodes
                edges.add(tuple(sorted(((i, j), neighbor))))
    return edges


def _connected_location_ids(network, location_ids):
    neighbors = {location_id: set() for location_id in location_ids}
    for link in network["links"]:
        neighbors[link["from"]].add(link["to"])
        neighbors[link["to"]].add(link["from"])
    reached = set()
    pending = [next(iter(location_ids))]
    while pending:
        location_id = pending.pop()
        if location_id in reached:
            continue
        reached.add(location_id)
        pending.extend(sorted(neighbors[location_id] - reached))
    return reached


def _connected_node_count(network):
    edges = _network_edges(network)
    if not edges:
        return 0
    neighbors = {}
    for a, b in edges:
        neighbors.setdefault(a, set()).add(b)
        neighbors.setdefault(b, set()).add(a)
    components = 0
    unseen = set(neighbors)
    while unseen:
        components += 1
        pending = [min(unseen)]
        while pending:
            node = pending.pop()
            if node not in unseen:
                continue
            unseen.remove(node)
            pending.extend(sorted(neighbors[node] & unseen))
    return components


def test_generation_is_byte_identical_for_fifty_seeds_and_seed_sensitive():
    coordinates = [(1, 1), (3, 2), (7, 4), (10, 3), (14, 7), (17, 12), (5, 15)]
    for seed in range(50):
        first = generate_road_network(_locations(coordinates), seed, 20)
        second = generate_road_network(_locations(coordinates), seed, 20)
        assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
            second, sort_keys=True, separators=(",", ":")
        )

    assert generate_road_network(_locations(coordinates), 1, 20) != generate_road_network(
        _locations(coordinates), 2, 20
    )


def test_network_connects_every_location_and_routes_links_between_gates():
    locations = _locations([(0, 0), (3, 1), (8, 3), (12, 9), (19, 19), (7, 15)])
    network = generate_road_network(locations, 404, 20)
    location_ids = {location["id"] for location in locations}

    assert _connected_location_ids(network, location_ids) == location_ids
    assert _connected_node_count(network) == 1
    nodes = {(node["i"], node["j"]): node for node in network["nodes"]}
    assert set(network["access"]) == location_ids
    for location in locations:
        x, y = location["coordinates"]["x"], location["coordinates"]["y"]
        gate = (x * SUBDIV + SUBDIV // 2, y * SUBDIV)
        assert network["access"][location["id"]] == {"side": "S", "node": list(gate)}
        assert gate in nodes
        assert nodes[gate]["mask"] in (EAST, WEST, EAST | WEST)
        assert location["connections"] == sorted(location["connections"])
    for link in network["links"]:
        assert tuple(link["path"][0]) == tuple(network["access"][link["from"]]["node"])
        assert tuple(link["path"][-1]) == tuple(network["access"][link["to"]]["node"])


def test_duplicate_cell_coordinates_raise_value_error():
    with pytest.raises(ValueError, match="one location per cell"):
        generate_road_network(_locations([(2, 3), (2, 3)]), 5, 20)


def test_every_gate_is_horizontal_and_present_across_seeds():
    coordinates = [(0, 0), (3, 1), (8, 3), (12, 9), (19, 19), (7, 15)]
    for seed in range(10):
        locations = _locations(coordinates)
        network = generate_road_network(locations, seed, 20)
        nodes = {(node["i"], node["j"]): node for node in network["nodes"]}
        for location in locations:
            x, y = location["coordinates"]["x"], location["coordinates"]["y"]
            gate = (x * SUBDIV + SUBDIV // 2, y * SUBDIV)
            assert gate in nodes
            assert nodes[gate]["mask"] in (EAST, WEST, EAST | WEST)
            assert network["access"][location["id"]]["node"] == list(gate)


def test_single_location_gets_west_stub_to_keep_its_gate_in_the_network():
    locations = _locations([(5, 6)])

    network = generate_road_network(locations, 41, 20)

    gate = (5 * SUBDIV + SUBDIV // 2, 6 * SUBDIV)
    nodes = {(node["i"], node["j"]): node for node in network["nodes"]}
    assert network["links"] == []
    assert gate in nodes
    assert nodes[gate]["mask"] == WEST
    assert _network_edges(network) == {tuple(sorted((gate, (gate[0] - 1, gate[1]))))}


def test_nodes_and_edges_are_on_cell_borders_and_tiles_round_trip():
    network = generate_road_network(_locations([(0, 0), (1, 2), (5, 6), (19, 18)]), 23, 20)
    nodes = {(node["i"], node["j"]): node for node in network["nodes"]}

    for (i, j), node in nodes.items():
        assert i % SUBDIV == 0 or j % SUBDIV == 0
        assert rotate_mask(BASE_MASKS[node["tile"]], node["rotation"]) == node["mask"]
        for di, dj, bit in ((0, 1, NORTH), (1, 0, EAST), (0, -1, SOUTH), (-1, 0, WEST)):
            if not node["mask"] & bit:
                continue
            neighbor = (i + di, j + dj)
            assert neighbor in nodes
            if di:
                assert j % SUBDIV == 0
            else:
                assert i % SUBDIV == 0


@pytest.mark.parametrize("tile", sorted(BASE_MASKS))
def test_rotate_mask_covers_each_tile_orientation(tile):
    base = BASE_MASKS[tile]
    for turns in range(4):
        rotated = rotate_mask(base, turns)
        assert rotate_mask(rotated, -turns) == base


def test_generation_scales_to_fifty_five_locations():
    locations = _locations([(index % 20, index // 20) for index in range(55)])

    started = time.perf_counter()
    network = generate_road_network(locations, 908, 20)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert len(network["nodes"]) < 2500
    assert _connected_node_count(network) == 1


def test_attach_location_keeps_links_and_existing_node_positions():
    locations = _locations([(1, 1), (4, 2), (8, 5), (11, 12)])
    network = generate_road_network(locations, 77, 20)
    previous_links = copy.deepcopy(network["links"])
    previous_node_positions = {(node["i"], node["j"]) for node in network["nodes"]}
    previous_edges = _network_edges(network)
    previous_masks = {(node["i"], node["j"]): node["mask"] for node in network["nodes"]}
    location = {
        "id": "location_new",
        "coordinates": {"x": 17, "y": 17},
        "connections": [],
    }

    result = attach_location(network, location, locations, 77, 20)

    assert result is network
    assert network["links"][: len(previous_links)] == previous_links
    assert previous_node_positions <= {(node["i"], node["j"]) for node in network["nodes"]}
    assert previous_edges <= _network_edges(network)
    assert location["connections"]
    assert _connected_node_count(network) == 1
    assert all(
        location["id"] in (link["from"], link["to"])
        for link in network["links"][len(previous_links) :]
    )
    gate = (
        location["coordinates"]["x"] * SUBDIV + SUBDIV // 2,
        location["coordinates"]["y"] * SUBDIV,
    )
    nodes = {(node["i"], node["j"]): node for node in network["nodes"]}
    assert gate in nodes
    new_link = network["links"][-1]
    assert new_link["from"] == location["id"]
    assert tuple(new_link["path"][0]) == gate
    assert tuple(new_link["path"][-1]) == tuple(network["access"][new_link["to"]]["node"])
    for node_position, old_mask in previous_masks.items():
        assert nodes[node_position]["mask"] & old_mask == old_mask
    for node in network["nodes"]:
        assert rotate_mask(BASE_MASKS[node["tile"]], node["rotation"]) == node["mask"]
