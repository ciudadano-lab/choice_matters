#!/usr/bin/env python3
"""Generate matching road tiles from the straight-road SVG."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
import resvg_py


ROOT = Path(__file__).resolve().parents[1]
ROAD_DIR = ROOT / "frontend" / "assets" / "roads"
SOURCE_SVG = ROAD_DIR / "road_straight.svg"
SOURCE_RASTER = ROAD_DIR / "road_straight_1254.png"
SIZE = 512
SOURCE_SIZE = 1254
ROAD_START = 61
ROAD_END = 451
KERB_WIDTH = 77
EXIT_SIDES = {
    "road_straight": ("N", "S"),
    "road_corner": ("S", "W"),
    "road_end": ("S",),
    "road_tee": ("W", "E", "S"),
    "road_cross": ("N", "E", "S", "W"),
}
SIDES = ("N", "E", "S", "W")


def rasterize_source() -> Image.Image:
    if not SOURCE_SVG.is_file():
        raise FileNotFoundError(f"Road source SVG not found: {SOURCE_SVG}")
    png = resvg_py.svg_to_bytes(
        svg_path=str(SOURCE_SVG),
        width=SOURCE_SIZE,
        height=SOURCE_SIZE,
    )
    source = Image.open(BytesIO(png)).convert("RGBA")
    if source.size != (SOURCE_SIZE, SOURCE_SIZE):
        raise ValueError(f"Unexpected raster size: {source.size}")
    source.save(SOURCE_RASTER)
    return source


def sample_bilinear(image: Image.Image, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Sample RGBA pixels with bilinear interpolation and premultiplied alpha."""
    pixels = np.asarray(image, dtype=np.float32)
    x = np.clip(x, 0, image.width - 1)
    y = np.clip(y, 0, image.height - 1)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, image.width - 1)
    y1 = np.minimum(y0 + 1, image.height - 1)
    wx = (x - x0)[..., None]
    wy = (y - y0)[..., None]

    alpha = pixels[..., 3:4] / 255.0
    premultiplied = np.concatenate((pixels[..., :3] * alpha, pixels[..., 3:4]), axis=2)
    top = premultiplied[y0, x0] * (1 - wx) + premultiplied[y0, x1] * wx
    bottom = premultiplied[y1, x0] * (1 - wx) + premultiplied[y1, x1] * wx
    result = top * (1 - wy) + bottom * wy

    result_alpha = result[..., 3:4]
    result_rgb = np.divide(
        result[..., :3] * 255,
        result_alpha,
        out=np.zeros_like(result[..., :3]),
        where=result_alpha > 0,
    )
    return np.concatenate((result_rgb, result_alpha), axis=2).clip(0, 255).astype(np.uint8)


def make_corner(source: Image.Image) -> Image.Image:
    coordinates = np.arange(SIZE, dtype=np.float32) + 0.5
    x, y = np.meshgrid(coordinates, coordinates)
    dx = x
    dy = SIZE - y
    radius = np.hypot(dx, dy)
    phi = np.arctan2(dy, dx)

    source_x = (radius / SIZE) * source.width
    source_y = (1 - phi / (np.pi / 2)) * (source.height - 1)
    pixels = sample_bilinear(source, source_x, source_y)
    valid = (radius < SIZE) & (phi >= 0) & (phi <= np.pi / 2)
    pixels[~valid] = 0
    return Image.fromarray(pixels, "RGBA")


def rotate_straight(straight: Image.Image, side: str) -> Image.Image:
    if side in ("N", "S"):
        return straight
    return straight.rotate(90 if side == "W" else 270, expand=True)


def make_arm(straight: Image.Image, side: str) -> tuple[Image.Image, tuple[int, int]]:
    oriented = rotate_straight(straight, side)
    if side == "N":
        return oriented.crop((0, 0, SIZE, ROAD_START)), (0, 0)
    if side == "S":
        return oriented.crop((0, ROAD_END, SIZE, SIZE)), (0, ROAD_END)
    if side == "W":
        return oriented.crop((0, 0, ROAD_START, SIZE)), (0, 0)
    return oriented.crop((ROAD_END, 0, SIZE, SIZE)), (ROAD_END, 0)


def make_junction(
    straight: Image.Image,
    exits: tuple[str, ...],
    asphalt_patch: Image.Image,
    kerb_vertical: Image.Image,
    kerb_corner: Image.Image,
) -> Image.Image:
    tile = Image.new("RGBA", (SIZE, SIZE))
    tile.alpha_composite(asphalt_patch, (ROAD_START, ROAD_START))

    for side in SIDES:
        if side in exits:
            arm, (x, y) = make_arm(straight, side)
            tile.alpha_composite(arm, (x, y))
            continue

        if side in ("N", "S"):
            band = kerb_vertical.rotate(90, expand=True)
            y = ROAD_START if side == "N" else ROAD_END - KERB_WIDTH + 1
            tile.alpha_composite(band, (ROAD_START, y))
        else:
            x = ROAD_START if side == "W" else ROAD_END - KERB_WIDTH + 1
            tile.alpha_composite(kerb_vertical, (x, ROAD_START))

    opposite_corner = {
        frozenset(("N", "E")): ("W", "S"),
        frozenset(("E", "S")): ("N", "W"),
        frozenset(("S", "W")): ("N", "E"),
        frozenset(("W", "N")): ("E", "S"),
    }
    for index, first in enumerate(SIDES):
        second = SIDES[(index + 1) % len(SIDES)]
        if first not in exits or second not in exits:
            continue
        horizontal_side, vertical_side = opposite_corner[frozenset((first, second))]
        x = ROAD_START if horizontal_side == "W" else ROAD_END - KERB_WIDTH + 1
        y = ROAD_START if vertical_side == "N" else ROAD_END - KERB_WIDTH + 1
        patch = kerb_corner
        if horizontal_side == "E":
            patch = ImageOps.mirror(patch)
        if vertical_side == "S":
            patch = ImageOps.flip(patch)
        tile.alpha_composite(patch, (x, y))

    return tile


def edge_pixels(tile: Image.Image, side: str) -> np.ndarray:
    alpha = np.asarray(tile.getchannel("A"))
    if side == "N":
        return alpha[0, :]
    if side == "S":
        return alpha[-1, :]
    if side == "W":
        return alpha[:, 0]
    return alpha[:, -1]


def check_tile(name: str, tile: Image.Image, exits: tuple[str, ...]) -> bool:
    passed = tile.size == (SIZE, SIZE) and tile.mode == "RGBA"
    print(f"{name}: {'PASS' if passed else 'FAIL'} - 512x512 RGBA")
    if not passed:
        return False

    all_passed = True
    for side in exits:
        indices = np.flatnonzero(edge_pixels(tile, side) > 0)
        if indices.size == 0:
            print(f"  {side} exit: FAIL - transparent at tile edge")
            all_passed = False
            continue

        low, high = int(indices[0]), int(indices[-1])
        center = (low + high) / 2
        span_ok = (
            abs(low - 0.12 * SIZE) <= 0.02 * SIZE
            and abs(high - 0.88 * SIZE) <= 0.02 * SIZE
            and abs(center - 0.5 * SIZE) <= 0.02 * SIZE
        )
        edge = edge_pixels(tile, side)
        reaches_edge = bool(edge[SIZE // 2] > 0)
        passed = span_ok and reaches_edge
        print(
            f"  {side} exit: {'PASS' if passed else 'FAIL'} - "
            f"span {low}..{high}, centre {center:.1f}, "
            f"{'road reaches tile edge' if reaches_edge else 'transparent centre at tile edge'}"
        )
        all_passed = all_passed and passed
    return all_passed


def make_preview(tiles: dict[str, Image.Image]) -> Image.Image:
    card_width = SIZE * 3
    card_height = SIZE * 3
    preview = Image.new("RGBA", (card_width * 3, card_height * 2), (70, 70, 70, 255))
    for index, (name, exits) in enumerate(EXIT_SIDES.items()):
        scene = Image.new("RGBA", (card_width, card_height))
        center_x = center_y = SIZE
        scene.alpha_composite(tiles[name], (center_x, center_y))
        for side in exits:
            straight = rotate_straight(tiles["road_straight"], side)
            location = {
                "N": (center_x, 0),
                "E": (SIZE * 2, center_y),
                "S": (center_x, SIZE * 2),
                "W": (0, center_y),
            }[side]
            scene.alpha_composite(straight, location)
        left = (index % 3) * card_width
        top = (index // 3) * card_height
        preview.alpha_composite(scene, (left, top))
    return preview


def main() -> None:
    ROAD_DIR.mkdir(parents=True, exist_ok=True)
    source = rasterize_source()
    straight = source.resize((SIZE, SIZE), Image.Resampling.LANCZOS)

    asphalt_half = straight.crop((140, ROAD_START, 248, ROAD_END + 1))
    asphalt_wide = Image.new("RGBA", (asphalt_half.width * 2, asphalt_half.height))
    asphalt_wide.alpha_composite(asphalt_half)
    asphalt_wide.alpha_composite(ImageOps.mirror(asphalt_half), (asphalt_half.width, 0))
    asphalt_patch = asphalt_wide.resize(
        (ROAD_END - ROAD_START + 1, ROAD_END - ROAD_START + 1),
        Image.Resampling.LANCZOS,
    )
    kerb_vertical = straight.crop((61, ROAD_START, 138, ROAD_END + 1))
    kerb_corner = straight.crop((61, 190, 61 + KERB_WIDTH, 190 + KERB_WIDTH))

    tiles = {
        "road_straight": straight,
        "road_corner": make_corner(source),
    }
    for name in ("road_end", "road_tee", "road_cross"):
        tiles[name] = make_junction(
            straight,
            EXIT_SIDES[name],
            asphalt_patch,
            kerb_vertical,
            kerb_corner,
        )

    passed = True
    for name, tile in tiles.items():
        tile.save(ROAD_DIR / f"{name}.png")
        passed = check_tile(name, tile, EXIT_SIDES[name]) and passed

    make_preview(tiles).save(ROAD_DIR / "road_tiles_preview.png")
    print(f"Preview: {ROAD_DIR / 'road_tiles_preview.png'}")
    if not passed:
        raise SystemExit("One or more road tile checks failed.")


if __name__ == "__main__":
    main()
