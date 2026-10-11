"""A real, drawn floor plan for the demo plant, tiled for the map (no sandbox/GDAL needed).

The map expects what `gdal2tiles.py -p raster` produces (backend/app/sandbox/convert.py): an XYZ
quadtree of 256 px PNG tiles, addressed TMS (y=0 at the bottom), with the image anchored at the
top-left of the world square and its native pixels at max zoom 6. Asset positions are pixels in that
max-zoom space (frontend `mapCoords.ts`), so this module owns both the drawing and
`ASSET_LAYOUT`, which keeps every marker on its own equipment pad.

    python -m scripts.demo_floorplan      # (re)build and upload the demo tiles
"""

from __future__ import annotations

import io
import json
import math
from concurrent.futures import ThreadPoolExecutor

from minio import Minio
from PIL import Image, ImageDraw, ImageFont

from app.core.config import settings
from app.core.storage import get_minio_client

TILE = 256
MAX_ZOOM = 6  # must equal RASTER_PROFILE_MAX_ZOOM in frontend FacilityMap.tsx
PLAN_W, PLAN_H = 4096, 3584
BACKGROUND = "#f8fafc"
# Zoom levels up to this get a tile for EVERY cell of the world grid (see tile_pyramid).
FILLER_MAX_ZOOM = 5
DEMO_TILE_PREFIX = "demo/demo-plant-1"

# asset name -> (x, y) pixels in the max-zoom image space
ASSET_LAYOUT: dict[str, tuple[float, float]] = {
    "Primary Feed Pump 7": (740.0, 1500.0),
    "Heat Exchanger Alpha": (1900.0, 1500.0),
    "Turbine Gen 1": (3200.0, 1500.0),
    "Cooling Fan 2": (1900.0, 2650.0),
    "Compressor Engine A (aged)": (520.0, 2700.0),
    "Compressor Engine B (new)": (1000.0, 2700.0),
}

# label, (x0, y0, x1, y1), fill
ZONES: list[tuple[str, tuple[int, int, int, int], str]] = [
    ("PUMP HOUSE", (240, 1000, 1240, 2000), "#eaf2fb"),
    ("THERMAL LOOP", (1400, 1000, 2400, 2000), "#fdf1e3"),
    ("POWER BLOCK", (2560, 1000, 3860, 2000), "#eaf6ee"),
    ("COOLING YARD", (1400, 2150, 2400, 3200), "#eef1f6"),
    ("COMPRESSOR HALL", (240, 2150, 1240, 3200), "#f3ecfa"),
]

_FONT_PATHS = ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = ("C:/Windows/Fonts/segoeuib.ttf", *(_FONT_PATHS)) if bold else _FONT_PATHS
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render_plan() -> Image.Image:
    img = Image.new("RGB", (PLAN_W, PLAN_H), BACKGROUND)
    d = ImageDraw.Draw(img)

    for x in range(0, PLAN_W, 256):  # faint survey grid
        d.line([(x, 0), (x, PLAN_H)], fill="#eef2f7", width=2)
    for y in range(0, PLAN_H, 256):
        d.line([(0, y), (PLAN_W, y)], fill="#eef2f7", width=2)

    d.rectangle((120, 120, PLAN_W - 120, PLAN_H - 120), outline="#475569", width=14)  # outer wall
    d.text((240, 200), "DEMO PLANT 1  \u2013  PROCESS FLOOR", fill="#0f172a", font=_font(92, True))
    d.text((240, 320), "Floor plan generated for the demo", fill="#64748b", font=_font(46))

    for label, (x0, y0, x1, y1), fill in ZONES:
        d.rounded_rectangle((x0, y0, x1, y1), radius=40, fill=fill, outline="#94a3b8", width=8)
        d.text((x0 + 40, y0 + 34), label, fill="#334155", font=_font(56, True))

    pump = ASSET_LAYOUT["Primary Feed Pump 7"]
    hx = ASSET_LAYOUT["Heat Exchanger Alpha"]
    turbine = ASSET_LAYOUT["Turbine Gen 1"]
    fan = ASSET_LAYOUT["Cooling Fan 2"]

    def pipe(points: list[tuple[float, float]], color: str, width: int = 26) -> None:
        d.line(points, fill=color, width=width, joint="curve")

    pipe([pump, hx], "#60a5fa")  # feed line
    pipe([hx, turbine], "#f59e0b")  # steam line
    corridor_y = 2075  # the gap between the two rows of zones
    pipe([pump, (pump[0], corridor_y), (fan[0], corridor_y), fan], "#94a3b8", 18)  # cooling branch
    for label, (x, y) in (
        ("FEED", (pump[0] + 560, pump[1] - 70)),
        ("STEAM", (hx[0] + 620, hx[1] - 70)),
    ):
        d.text((x - 60, y), label, fill="#475569", font=_font(40, True))

    for name, (x, y) in ASSET_LAYOUT.items():  # an equipment pad under every marker
        d.rounded_rectangle(
            (x - 70, y - 70, x + 70, y + 70), radius=18, fill="#ffffff", outline="#64748b", width=8
        )
        d.text(
            (x, y + 110),
            name.replace("Compressor ", ""),
            fill="#334155",
            font=_font(40, True),
            anchor="mt",
        )

    d.text((PLAN_W - 520, PLAN_H - 300), "N \u2191", fill="#475569", font=_font(70, True))
    d.line([(240, PLAN_H - 220), (240 + 512, PLAN_H - 220)], fill="#475569", width=12)
    d.text((240, PLAN_H - 190), "10 m", fill="#475569", font=_font(40))
    return img


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


def upload_tiles(
    tiles: dict[str, bytes], prefix: str = DEMO_TILE_PREFIX, client: Minio | None = None
) -> int:
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

    with ThreadPoolExecutor(max_workers=8) as pool:  # ~1,500 small objects; client is thread-safe
        list(pool.map(put, tiles.items()))
    return len(tiles)


def build_and_upload() -> int:
    return upload_tiles(tile_pyramid(render_plan()))


if __name__ == "__main__":
    count = build_and_upload()
    print(f"uploaded {count} tiles under {settings.minio_tiles_bucket}/{DEMO_TILE_PREFIX}")
