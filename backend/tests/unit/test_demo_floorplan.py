"""The multi-floor plans and their tile pyramids: addressing, coverage, alignment with the spec."""

import io

import pytest
from PIL import Image

from scripts.demo_factory import FLOORS, MACHINES, PLAN_H, PLAN_W, machines_on
from scripts.demo_floorplan import (
    BACKGROUND,
    FILLER_MAX_ZOOM,
    MAX_ZOOM,
    TILE,
    render_floor,
    tile_pyramid,
)

PLANS = {floor.level: render_floor(floor.level) for floor in FLOORS}
# Tiling one floor is enough to test the pyramid mechanics (it is identical code per floor).
TILES = tile_pyramid(PLANS[2])


def _png(key: str) -> Image.Image:
    return Image.open(io.BytesIO(TILES[key])).convert("RGB")


@pytest.mark.parametrize("level", [0, 1, 2])
def test_every_floor_is_drawn_at_the_expected_size(level: int) -> None:
    assert PLANS[level].size == (PLAN_W, PLAN_H)


@pytest.mark.parametrize("level", [0, 1, 2])
def test_every_machine_sits_on_a_white_pad_on_its_own_floor(level: int) -> None:
    """Markers are positioned in this pixel space, so the pad must be exactly under them."""
    plan = PLANS[level]
    for m in machines_on(level):
        # just inside the top and bottom edges, clear of every symbol, the pad is plain white
        for dx, dy in ((-60, -105), (60, -105), (-60, 105)):
            assert plan.getpixel((int(m.x + dx), int(m.y + dy))) == (255, 255, 255), (m.key, dx, dy)


def test_a_machine_is_only_drawn_on_its_own_floor() -> None:
    """The ground-floor plan must not show a roof machine at that machine's coordinates."""
    ground, roof = PLANS[0], PLANS[2]
    cooling_tower = next(m for m in MACHINES if m.key == "CT-1")
    spot = (int(cooling_tower.x - 60), int(cooling_tower.y - 105))
    assert roof.getpixel(spot) == (255, 255, 255)
    assert ground.getpixel(spot) != (255, 255, 255)


def test_every_cell_of_the_world_grid_has_a_tile_up_to_the_filler_zoom() -> None:
    """A missing tile is a 404 and shows as a black void beside the plan."""
    for z in range(FILLER_MAX_ZOOM + 1):
        n = 2**z
        for x in range(n):
            for y in range(n):
                assert f"{z}/{x}/{y}.png" in TILES, (z, x, y)


def test_plan_tiles_exist_down_to_max_zoom_and_are_256_px() -> None:
    cols, rows = PLAN_W // TILE, PLAN_H // TILE
    assert sum(1 for k in TILES if k.startswith(f"{MAX_ZOOM}/")) == cols * rows
    assert all(_png(k).size == (TILE, TILE) for k in list(TILES)[:50])


def test_tiles_are_addressed_tms_with_the_image_at_the_top_left() -> None:
    top = f"{MAX_ZOOM}/0/{2**MAX_ZOOM - 1}.png"  # the top image row is the highest TMS y
    assert top in TILES
    far = _png("3/7/0.png")  # nowhere near the plan's corner at zoom 3: filler
    assert far.getpixel((100, 100)) == Image.new("RGB", (1, 1), BACKGROUND).getpixel((0, 0))


def test_a_max_zoom_tile_is_exactly_the_matching_crop_of_the_plan() -> None:
    """Tile (x, TMS y) at max zoom is the plan pixels it claims to be - this is what keeps markers
    (positioned in the same pixel space) on their equipment."""
    col, row = 5, 9
    y_tms = (2**MAX_ZOOM - 1) - row
    expected = PLANS[2].crop((col * TILE, row * TILE, (col + 1) * TILE, (row + 1) * TILE))
    assert list(_png(f"{MAX_ZOOM}/{col}/{y_tms}.png").getdata()) == list(expected.getdata())
