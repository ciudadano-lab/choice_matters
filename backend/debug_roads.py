from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from world_state import GRID_SIZE, generate_initial_world_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a generated road network.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--output",
        type=Path,
        help="PNG path (default: backend/roads_seed_<seed>.png)",
    )
    args = parser.parse_args()

    output = args.output or Path(__file__).with_name(f"roads_seed_{args.seed}.png")
    world = generate_initial_world_state([], args.seed)
    network = world.cityLayout["roadNetwork"]
    subdiv = network["subdiv"]
    grid_size = world.cityLayout.get("gridSize", GRID_SIZE)

    fig, axis = plt.subplots(figsize=(16, 16))
    for cell in range(grid_size + 1):
        axis.plot([cell, cell], [0, grid_size], color="#d5d5d5", linewidth=0.4, zorder=0)
        axis.plot([0, grid_size], [cell, cell], color="#d5d5d5", linewidth=0.4, zorder=0)

    nodes = {(node["i"], node["j"]): node for node in network["nodes"]}
    direction_bits = ((0, 1, 1), (1, 0, 2), (0, -1, 4), (-1, 0, 8))
    for (i, j), node in nodes.items():
        for di, dj, bit in direction_bits:
            if not node["mask"] & bit:
                continue
            neighbor = (i + di, j + dj)
            if neighbor not in nodes:
                continue
            if (j, i) < (neighbor[1], neighbor[0]):
                axis.plot(
                    [i / subdiv, neighbor[0] / subdiv],
                    [j / subdiv, neighbor[1] / subdiv],
                    color="#343a40",
                    linewidth=1.5,
                    zorder=1,
                )
        axis.text(
            i / subdiv,
            j / subdiv,
            node["tile"],
            fontsize=3.5,
            ha="center",
            va="center",
            color="#b42318",
            zorder=2,
        )

    for location in world.locations:
        x, y = location["coordinates"]["x"], location["coordinates"]["y"]
        axis.scatter(x + 0.5, y + 0.5, s=12, color="#175cd3", zorder=3)
        axis.text(x + 0.53, y + 0.53, location["id"], fontsize=4, color="#175cd3", zorder=3)
        gate_i, gate_j = network["access"][location["id"]]["node"]
        axis.scatter(
            gate_i / subdiv,
            gate_j / subdiv,
            s=8,
            marker="s",
            color="#f79009",
            edgecolors="#7a2e0e",
            linewidths=0.3,
            zorder=4,
        )

    axis.set_xlim(0, grid_size)
    axis.set_ylim(0, grid_size)
    axis.set_aspect("equal")
    axis.set_title(f"{world.cityLayout['cityName']} - road network (seed {args.seed})")
    axis.set_xlabel("World X")
    axis.set_ylabel("World Y (north is up)")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved road network visualization to {output}")


if __name__ == "__main__":
    main()
