"""Draws and tiles the Meridian plant's floor plans (no sandbox/GDAL needed).

One architectural-style drawing per floor (walls, areas, doors, column grid, a symbol for each
machine, and the service lines that connect them) from the specification in `demo_factory`, cut into
what `gdal2tiles.py -p raster` produces (backend/app/sandbox/convert.py): an XYZ quadtree of 256 px
PNG tiles addressed TMS (y=0 at the bottom), the image anchored at the top-left of the world square
with its native pixels at max zoom 6. Machine positions are pixels in that same space (frontend
`mapCoords.ts`), so every marker lands on its own symbol.

    python -m scripts.demo_floorplan      # (re)build and upload the tiles for every floor
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from minio import Minio
from PIL import Image, ImageDraw, ImageFont

from app.core.config import settings
from app.core.storage import get_minio_client
from scripts.demo_factory import (
    AREAS,
    BY_KEY,
    FLOORS,
    LINK_COLORS,
    LINKS,
    PLAN_H,
    PLAN_W,
    PLANT_NAME,
    Machine,
    machines_on,
    tile_prefix,
)

TILE = 256
MAX_ZOOM = 6  # must equal RASTER_PROFILE_MAX_ZOOM in frontend FacilityMap.tsx
BACKGROUND = "#f8fafc"
# Zoom levels up to this get a tile for EVERY cell of the world grid (see tile_pyramid).
FILLER_MAX_ZOOM = 5

INK = "#334155"
MUTED = "#64748b"
PAD = 120  # half-size of the white equipment pad under each machine

_REGULAR = ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf")
_BOLD = ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _BOLD if bold else _REGULAR:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


# ---- equipment symbols -----------------------------------------------------------------------

Draw = ImageDraw.ImageDraw


def _line(d: Draw, pts: list[tuple[float, float]], w: int = 7) -> None:
    d.line(pts, fill=INK, width=w, joint="curve")


def _blades(d: Draw, x: float, y: float, r: float, n: int, w: int = 8) -> None:
    for k in range(n):
        a = math.tau * k / n + 0.4
        _line(d, [(x, y), (x + r * math.cos(a), y + r * math.sin(a))], w)


def _sym_pump(d: Draw, x: float, y: float) -> None:
    d.ellipse((x - 70, y - 70, x + 70, y + 70), outline=INK, width=8)
    _blades(d, x, y, 52, 3)
    d.rectangle((x + 70, y - 16, x + 108, y + 16), outline=INK, width=7)


def _sym_chiller(d: Draw, x: float, y: float) -> None:
    d.rounded_rectangle((x - 90, y - 58, x + 90, y + 58), radius=12, outline=INK, width=8)
    for k in range(3):
        a = math.pi * k / 3
        _line(
            d,
            [
                (x - 42 * math.cos(a), y - 42 * math.sin(a)),
                (x + 42 * math.cos(a), y + 42 * math.sin(a)),
            ],
        )


def _sym_boiler(d: Draw, x: float, y: float) -> None:
    d.rounded_rectangle((x - 58, y - 88, x + 58, y + 88), radius=26, outline=INK, width=8)
    d.polygon([(x, y + 64), (x - 26, y + 20), (x, y - 6), (x + 26, y + 20)], fill="#f59e0b")
    d.polygon([(x, y + 56), (x - 12, y + 28), (x, y + 14), (x + 12, y + 28)], fill="#fde68a")


def _sym_compressor(d: Draw, x: float, y: float) -> None:
    d.ellipse((x - 85, y - 60, x + 35, y + 60), outline=INK, width=8)
    d.ellipse((x - 55, y - 30, x + 5, y + 30), outline=INK, width=6)
    d.rectangle((x + 35, y - 36, x + 95, y + 36), outline=INK, width=8)


def _sym_cnc(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 92, y - 72, x + 92, y + 72), outline=INK, width=8)
    d.rectangle((x - 62, y - 44, x + 8, y + 30), outline=INK, width=6, fill="#dbeafe")
    _line(d, [(x + 46, y - 62), (x + 46, y + 4)], 9)
    d.ellipse((x + 36, y + 4, x + 56, y + 24), fill=INK)


def _sym_hydraulic(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 88, y - 34, x + 6, y + 66), outline=INK, width=8)
    d.ellipse((x + 18, y - 56, x + 92, y + 18), outline=INK, width=8)
    _line(d, [(x + 6, y), (x + 18, y - 20)])


def _sym_conveyor(d: Draw, x: float, y: float) -> None:
    d.rounded_rectangle((x - 160, y - 38, x + 160, y + 38), radius=36, outline=INK, width=8)
    for k in range(-3, 4):
        d.ellipse((x + k * 46 - 13, y - 13, x + k * 46 + 13, y + 13), outline=INK, width=5)


def _sym_robot(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 92, y - 92, x + 92, y + 92), outline=MUTED, width=6)
    d.ellipse((x - 28, y + 20, x + 28, y + 76), outline=INK, width=8)
    _line(d, [(x, y + 48), (x + 44, y - 20), (x + 92, y - 58)], 11)
    d.ellipse((x + 82, y - 68, x + 102, y - 48), fill=INK)


def _sym_pickplace(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 92, y - 70, x + 92, y + 70), outline=INK, width=8)
    _line(d, [(x - 92, y - 30), (x + 92, y - 30)], 8)
    d.rectangle((x - 22, y - 30, x + 22, y + 40), outline=INK, width=7)


def _sym_booth(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 92, y - 68, x + 92, y + 68), outline=INK, width=8)
    for k in (-1, 0, 1):
        _line(d, [(x - 60, y - 30 + k * 30), (x + 20, y - 10 + k * 30)], 6)
        d.ellipse((x + 28, y - 18 + k * 30, x + 44, y - 2 + k * 30), fill="#0ea5e9")


def _sym_oven(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 92, y - 68, x + 92, y + 68), outline=INK, width=8)
    pts = [(x - 62 + k * 20, y + (28 if k % 2 else -28)) for k in range(0, 7)]
    d.line(pts, fill="#ef4444", width=8, joint="curve")


def _sym_fan(d: Draw, x: float, y: float) -> None:
    d.ellipse((x - 70, y - 70, x + 70, y + 70), outline=INK, width=8)
    _blades(d, x, y, 56, 4, 11)
    d.ellipse((x - 10, y - 10, x + 10, y + 10), fill=INK)


def _sym_ahu(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 108, y - 58, x + 108, y + 58), outline=INK, width=8)
    d.ellipse((x - 92, y - 40, x - 12, y + 40), outline=INK, width=7)
    _blades(d, x - 52, y, 34, 3, 6)
    for k in range(4):
        _line(d, [(x + 18 + k * 20, y - 44), (x + 18 + k * 20, y + 44)], 5)


def _sym_tower(d: Draw, x: float, y: float) -> None:
    d.polygon(
        [(x - 78, y + 78), (x + 78, y + 78), (x + 54, y - 24), (x - 54, y - 24)],
        outline=INK,
        width=8,
    )
    d.ellipse((x - 46, y - 82, x + 46, y + 6), outline=INK, width=7)
    _blades(d, x, y - 38, 34, 3, 6)
    for k in range(3):
        _line(d, [(x - 38, y + 22 + k * 18), (x + 38, y + 22 + k * 18)], 4)


def _sym_transformer(d: Draw, x: float, y: float) -> None:
    d.ellipse((x - 70, y - 44, x + 10, y + 36), outline=INK, width=8)
    d.ellipse((x - 10, y - 44, x + 70, y + 36), outline=INK, width=8)
    _line(d, [(x - 30, y + 36), (x - 30, y + 74)])
    _line(d, [(x + 30, y + 36), (x + 30, y + 74)])


def _sym_switchgear(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 100, y - 66, x + 100, y + 66), outline=INK, width=8)
    for k in range(-1, 2):
        _line(d, [(x + k * 50 + 25, y - 66), (x + k * 50 + 25, y + 66)], 5)
    for k in range(-2, 2):
        d.rectangle((x + k * 50 + 8, y - 24, x + k * 50 + 38, y + 6), fill=INK)


def _sym_ups(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 92, y - 62, x + 92, y + 62), outline=INK, width=8)
    for k in range(3):
        d.rectangle(
            (x - 60 + k * 44, y - 34, x - 28 + k * 44, y + 34), outline=INK, width=6, fill="#bbf7d0"
        )


def _sym_generator(d: Draw, x: float, y: float) -> None:
    d.ellipse((x - 68, y - 68, x + 68, y + 68), outline=INK, width=8)
    d.text((x, y), "G", fill=INK, font=_font(84, True), anchor="mm")


def _sym_tester(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 90, y - 64, x + 90, y + 64), outline=INK, width=8)
    d.line(
        [(x - 40, y), (x - 8, y + 34), (x + 46, y - 36)], fill="#16a34a", width=11, joint="curve"
    )


def _sym_packer(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 94, y - 60, x + 94, y + 60), outline=INK, width=8)
    for k in range(3):
        d.rectangle((x - 70 + k * 50, y - 24, x - 38 + k * 50, y + 24), outline=INK, width=6)


def _sym_palletizer(d: Draw, x: float, y: float) -> None:
    for k in range(3):
        d.rectangle((x - 80 + k * 54, y + 8, x - 36 + k * 54, y + 56), outline=INK, width=7)
    for k in range(2):
        d.rectangle((x - 54 + k * 54, y - 44, x - 10 + k * 54, y + 4), outline=INK, width=7)


def _sym_hoist(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 60, y - 90, x + 60, y + 90), outline=INK, width=8)
    d.polygon([(x, y - 66), (x - 28, y - 22), (x + 28, y - 22)], fill=INK)
    d.polygon([(x, y + 66), (x - 28, y + 22), (x + 28, y + 22)], fill=INK)


def _sym_default(d: Draw, x: float, y: float) -> None:
    d.rectangle((x - 80, y - 60, x + 80, y + 60), outline=INK, width=8)


SYMBOLS: dict[str, Callable[[Draw, float, float], None]] = {
    "pump": _sym_pump,
    "chiller": _sym_chiller,
    "boiler": _sym_boiler,
    "air_compressor": _sym_compressor,
    "cnc_machine": _sym_cnc,
    "hydraulic_unit": _sym_hydraulic,
    "conveyor": _sym_conveyor,
    "robot_cell": _sym_robot,
    "pick_place": _sym_pickplace,
    "paint_booth": _sym_booth,
    "oven": _sym_oven,
    "fan": _sym_fan,
    "air_handler": _sym_ahu,
    "cooling_tower": _sym_tower,
    "transformer": _sym_transformer,
    "switchgear": _sym_switchgear,
    "ups": _sym_ups,
    "generator": _sym_generator,
    "tester": _sym_tester,
    "packer": _sym_packer,
    "palletizer": _sym_palletizer,
    "hoist": _sym_hoist,
}


# ---- drawing a floor -------------------------------------------------------------------------


def _elbow(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
    """Horizontal then vertical, like a pipe run; straight if the machines line up."""
    if abs(a[0] - b[0]) < 1 or abs(a[1] - b[1]) < 1:
        return [a, b]
    return [a, (b[0], a[1]), b]


def _arrow_head(d: Draw, tip: tuple[float, float], frm: tuple[float, float], color: str) -> None:
    ang = math.atan2(tip[1] - frm[1], tip[0] - frm[0])
    pts = [tip]
    for da in (2.6, -2.6):
        pts.append((tip[0] + 46 * math.cos(ang + da), tip[1] + 46 * math.sin(ang + da)))
    d.polygon(pts, fill=color)


def _short_name(m: Machine) -> str:
    return m.name.replace(m.key, "").strip()


def render_floor(level: int) -> Image.Image:
    floor = next(f for f in FLOORS if f.level == level)
    img = Image.new("RGB", (PLAN_W, PLAN_H), BACKGROUND)
    d = ImageDraw.Draw(img)

    for x in range(0, PLAN_W, 256):  # faint survey grid
        d.line([(x, 0), (x, PLAN_H)], fill="#eef2f7", width=2)
    for y in range(0, PLAN_H, 256):
        d.line([(0, y), (PLAN_W, y)], fill="#eef2f7", width=2)

    d.rectangle((200, 200, PLAN_W - 200, PLAN_H - 200), outline="#475569", width=22)  # outer wall
    for k, x in enumerate(range(300, PLAN_W - 200, 1000)):  # structural column grid
        d.text((x, 235), chr(65 + k), fill=MUTED, font=_font(40, True), anchor="mt")
        for y in (800, 1800, 2800, 3600, 4600, 5300):
            d.rectangle((x - 15, y - 15, x + 15, y + 15), fill="#94a3b8")

    d.text((300, 300), f"{PLANT_NAME.upper()}", fill="#0f172a", font=_font(88, True))
    d.text((300, 410), f"{floor.name}  \u00b7  Level {level}", fill=INK, font=_font(58, True))
    d.text((300, 490), "Floor plan generated for demonstration", fill=MUTED, font=_font(38))

    # legend of service lines
    lx = 3300
    d.text((lx, 300), "SERVICES", fill=MUTED, font=_font(34, True))
    for i, (kind, color) in enumerate(LINK_COLORS.items()):
        x = lx + (i % 4) * 520
        y = 360 + (i // 4) * 80
        d.line([(x, y + 22), (x + 90, y + 22)], fill=color, width=14)
        d.text((x + 110, y), kind.title(), fill=INK, font=_font(38))

    # lift and stairs (the floor-to-floor connections)
    for label, x in (("LIFT", 7050), ("STAIRS", 7500)):
        d.rectangle((x - 140, 300, x + 140, 560), outline="#475569", width=8, fill="#ffffff")
        d.text((x, 430), label, fill=INK, font=_font(54, True), anchor="mm")

    for area in (a for a in AREAS if a.floor == level):
        x0, y0, x1, y1 = area.rect
        d.rounded_rectangle(
            (x0, y0, x1, y1), radius=36, fill=area.color, outline="#94a3b8", width=8
        )
        d.text((x0 + 44, y0 + 34), area.label, fill=INK, font=_font(56, True))
        # a door in the bottom wall
        dx = (x0 + x1) // 2
        d.rectangle((dx - 90, y1 - 6, dx + 90, y1 + 6), fill=BACKGROUND)
        d.arc((dx - 90, y1 - 90, dx + 90, y1 + 90), 270, 360, fill="#94a3b8", width=5)

    here = machines_on(level)
    here_keys = {m.key for m in here}

    # service lines (under the machines), blended so crossings stay readable
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for dependent, upstream, kind in LINKS:
        if dependent in here_keys and upstream in here_keys:
            a, b = (
                (BY_KEY[upstream].x, BY_KEY[upstream].y),
                (BY_KEY[dependent].x, BY_KEY[dependent].y),
            )
            pts = _elbow(a, b)
            color = LINK_COLORS[kind]
            od.line(pts, fill=color + "c8", width=16, joint="curve")
            _arrow_head(od, b, pts[-2], color + "ff")
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
    d = ImageDraw.Draw(img)

    for m in here:
        d.rounded_rectangle(
            (m.x - PAD, m.y - PAD, m.x + PAD, m.y + PAD),
            radius=24,
            fill="#ffffff",
            outline=MUTED,
            width=8,
        )
        SYMBOLS.get(m.type, _sym_default)(d, m.x, m.y)
        d.text((m.x, m.y + PAD + 18), m.key, fill="#0f172a", font=_font(58, True), anchor="mt")
        d.text((m.x, m.y + PAD + 86), _short_name(m), fill=MUTED, font=_font(36), anchor="mt")

        # tags for connections to other floors
        tags: list[tuple[str, str]] = []
        for dependent, upstream, kind in LINKS:
            if dependent == m.key and BY_KEY[upstream].floor != level:
                other = BY_KEY[upstream]
                tags.append((f"from {other.key} (L{other.floor})", LINK_COLORS[kind]))
            elif upstream == m.key and BY_KEY[dependent].floor != level:
                other = BY_KEY[dependent]
                tags.append((f"to {other.key} (L{other.floor})", LINK_COLORS[kind]))
        for i, (text, color) in enumerate(tags[:3]):
            d.text(
                (m.x, m.y - PAD - 20 - i * 44), text, fill=color, font=_font(36, True), anchor="mb"
            )

    bar_y = PLAN_H - 262  # inside the margin between the last room and the outer wall
    d.line([(300, bar_y), (1300, bar_y)], fill="#475569", width=12)  # scale bar
    d.text(
        (1340, bar_y),
        f"{round(1000 * 120 / PLAN_W)} m",
        fill="#475569",
        font=_font(34),
        anchor="lm",
    )
    d.text((PLAN_W - 520, PLAN_H - 330), "N \u2191", fill="#475569", font=_font(66, True))
    return img


# ---- tiling and upload -----------------------------------------------------------------------


def tile_pyramid(img: Image.Image) -> dict[str, bytes]:
    """`{z}/{x}/{y_tms}.png` -> PNG bytes for the tiles that overlap the image, zoom 0..MAX_ZOOM.

    At zoom z the image is shrunk by 2^(z - MAX_ZOOM) and anchored at the world square's top-left;
    rows are numbered from the top (XYZ) here and flipped to TMS (y=0 at the bottom) on output.
    """
    tiles: dict[str, bytes] = {}
    for z in range(MAX_ZOOM + 1):
        scale = 2.0 ** (z - MAX_ZOOM)
        w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
        level = img.resize((w, h), Image.Resampling.LANCZOS) if z != MAX_ZOOM else img
        for row in range(math.ceil(h / TILE)):
            for col in range(math.ceil(w / TILE)):
                crop = level.crop((col * TILE, row * TILE, (col + 1) * TILE, (row + 1) * TILE))
                tile = Image.new("RGB", (TILE, TILE), BACKGROUND)
                tile.paste(crop, (0, 0))
                buf = io.BytesIO()
                tile.save(buf, "PNG", optimize=True)
                y_tms = (2**z - 1) - row
                tiles[f"{z}/{col}/{y_tms}.png"] = buf.getvalue()

    # The plan fills only the top-left corner of the world grid. A missing tile is a 404, which
    # the map shows as a black void beside the plan, so give every cell of the grid up to
    # FILLER_MAX_ZOOM a plain background tile (one shared PNG, so this costs nothing to build).
    blank = io.BytesIO()
    Image.new("RGB", (TILE, TILE), BACKGROUND).save(blank, "PNG", optimize=True)
    for z in range(FILLER_MAX_ZOOM + 1):
        n = 2**z
        for col in range(n):
            for y_tms in range(n):
                tiles.setdefault(f"{z}/{col}/{y_tms}.png", blank.getvalue())
    return tiles


def _public_read_policy(bucket: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
    )


def upload_tiles(tiles: dict[str, bytes], prefix: str, client: Minio | None = None) -> int:
    client = client or get_minio_client()
    bucket = settings.minio_tiles_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.set_bucket_policy(bucket, _public_read_policy(bucket))  # tiles are fetched by URL

    def put(item: tuple[str, bytes]) -> None:
        key, data = item
        client.put_object(
            bucket, f"{prefix}/{key}", io.BytesIO(data), length=len(data), content_type="image/png"
        )

    with ThreadPoolExecutor(
        max_workers=8
    ) as pool:  # thousands of small objects; client is thread-safe
        list(pool.map(put, tiles.items()))
    return len(tiles)


def build_and_upload() -> dict[int, int]:
    """Render, tile and upload every floor; returns tiles uploaded per level."""
    return {
        floor.level: upload_tiles(tile_pyramid(render_floor(floor.level)), tile_prefix(floor.level))
        for floor in FLOORS
    }


if __name__ == "__main__":
    counts = build_and_upload()
    print(f"uploaded tiles to {settings.minio_tiles_bucket}: {counts}")
