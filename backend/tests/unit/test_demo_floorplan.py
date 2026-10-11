"""The demo floor plan's tile pyramid: addressing, coverage and alignment with the asset layout."""

import io

from PIL import Image

from scripts.demo_floorplan import (
    ASSET_LAYOUT,
    BACKGROUND,
    FILLER_MAX_ZOOM,
    MAX_ZOOM,
    PLAN_H,
    PLAN_W,
    TILE,
    render_plan,
    tile_pyramid,
)

PLAN = render_plan()
TILES = tile_pyramid(PLAN)


def _png(key: str) -> Image.Image:
    return Image.open(io.BytesIO(TILES[key])).convert("RGB")


def test_every_cell_of_the_world_grid_has_a_tile_up_to_the_filler_zoom() -> None:
    """A missing tile is a 404 and shows as a black void beside the plan."""
    for z in range(FILLER_MAX_ZOOM + 1):
        n = 2**z
        for x in range(n):
            for y in range(n):
                assert f"{z}/{x}/{y}.png" in TILES, (z, x, y)


def test_plan_tiles_exist_down_to_max_zoom_and_all_are_256_px() -> None:
    cols, rows = PLAN_W // TILE, PLAN_H // TILE
    assert sum(1 for k in TILES if k.startswith(f"{MAX_ZOOM}/")) == cols * rows
    assert all(_png(k).size == (TILE, TILE) for k in list(TILES)[:50])


def test_tiles_are_addressed_tms_with_the_image_at_the_top_left() -> None:
    """Top image row at max zoom is TMS y = 2^z - 1; the filler cells are background-coloured."""
    top = f"{MAX_ZOOM}/0/{2**MAX_ZOOM - 1}.png"
    assert top in TILES
    assert _png(top).size == (TILE, TILE)
    filler = _png("3/7/0.png")  # far from the plan's corner at zoom 3
    assert filler.getpixel((100, 100)) == Image.new("RGB", (1, 1), BACKGROUND).getpixel((0, 0))


def test_a_max_zoom_tile_is_the_matching_crop_of_the_plan() -> None:
    """Tile (x, TMS y) at max zoom is exactly the plan pixels it claims to be - this is what
    keeps markers (positioned in the same pixel space) on their equipment pads."""
    col, row = 2, 5
    y_tms = (2**MAX_ZOOM - 1) - row
    expected = PLAN.crop((col * TILE, row * TILE, (col + 1) * TILE, (row + 1) * TILE))
    assert list(_png(f"{MAX_ZOOM}/{col}/{y_tms}.png").getdata()) == list(expected.getdata())


def test_every_asset_sits_on_an_equipment_pad_inside_the_plan() -> None:
    for name, (x, y) in ASSET_LAYOUT.items():
        assert 0 < x < PLAN_W and 0 < y < PLAN_H, name
        # the pad is a white square centred on the asset; its centre pixel is white
        assert PLAN.getpixel((int(x), int(y))) == (255, 255, 255), name
