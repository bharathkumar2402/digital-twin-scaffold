"""GDAL conversion pipeline (issue 2.3): rasterize a sanitized floor plan (SVG/DXF/PDF)
to a single PNG, then tile it into an XYZ pyramid for the tile server (task 2.4).

Floor plans have no real-world coordinate reference system, so tiles are generated
with gdal2tiles.py's "raster" profile — it tiles an arbitrary image in local pixel
XYZ coordinates instead of reprojecting to a geographic CRS like WGS84 (see
PROJECT_PLAN.md §6: "Coordinates are in a local pixel or UTM coordinate system").

Every third-party rasterization/GDAL dependency is imported lazily, inside the
function that uses it, never at module import time. That's not a style preference:
this module is exercised by unit tests on hosts (including Windows dev machines)
that don't have GDAL, cairo, or the other native libraries the sandbox Docker image
(Dockerfile.sandbox, built from an osgeo/gdal base) actually ships — a top-level
import would make the module itself unimportable there. Tests inject a fake
TilePyramidBuilder and monkeypatch RASTERIZERS instead of exercising the real
native calls; see tests/unit/test_sandbox_convert.py.
"""

import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

DEFAULT_MAX_ZOOM = 4
GDAL2TILES_TIMEOUT_SECONDS = 120


class TileConversionError(Exception):
    """Raised for any failure turning sanitized bytes into a tile pyramid —
    unsupported/unrasterizable content or a failing gdal2tiles.py invocation."""


def _rasterize_svg(content: bytes) -> bytes:
    import cairosvg

    png_bytes = cairosvg.svg2png(bytestring=content)
    if png_bytes is None:
        raise TileConversionError("cairosvg produced no output for this SVG")
    return png_bytes


def _rasterize_dxf(content: bytes) -> bytes:
    import io

    import ezdxf
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    with tempfile.NamedTemporaryFile(suffix=".dxf") as tmp:
        tmp.write(content)
        tmp.flush()
        doc = ezdxf.readfile(tmp.name)

    figure = plt.figure()
    try:
        axes = figure.add_axes((0, 0, 1, 1))
        axes.set_axis_off()
        Frontend(RenderContext(doc), MatplotlibBackend(axes)).draw_layout(
            doc.modelspace(), finalize=True
        )
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=200)
        return buffer.getvalue()
    finally:
        plt.close(figure)


def _rasterize_pdf(content: bytes) -> bytes:
    import pymupdf  # PyMuPDF's current import name (the "fitz" alias is deprecated)

    with pymupdf.open(stream=content, filetype="pdf") as doc:
        if doc.page_count == 0:
            raise TileConversionError("PDF has no pages to rasterize")
        pixmap = doc[0].get_pixmap(dpi=200)
        return pixmap.tobytes("png")


RASTERIZERS = {
    "svg": _rasterize_svg,
    "dxf": _rasterize_dxf,
    "pdf": _rasterize_pdf,
}


def rasterize_to_png(content: bytes, extension: str) -> bytes:
    try:
        rasterizer = RASTERIZERS[extension.lower()]
    except KeyError as exc:
        raise TileConversionError(
            f"no rasterizer for extension {extension!r}"
        ) from exc
    return rasterizer(content)


class TilePyramidBuilder(Protocol):
    def build(self, png_bytes: bytes, out_dir: Path, *, max_zoom: int) -> None: ...


class GdalRasterTilePyramidBuilder:
    """Shells out to gdal2tiles.py rather than importing the osgeo Python bindings —
    only present in the sandbox's Docker image (Dockerfile.sandbox, osgeo/gdal base),
    and shelling out keeps this module importable without them (see module docstring).
    """

    def build(self, png_bytes: bytes, out_dir: Path, *, max_zoom: int = DEFAULT_MAX_ZOOM) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "source.png"
            source_path.write_bytes(png_bytes)
            result = subprocess.run(
                [
                    "gdal2tiles.py",
                    "-p",
                    "raster",
                    "-z",
                    f"0-{max_zoom}",
                    "-w",
                    "none",
                    str(source_path),
                    str(out_dir),
                ],
                capture_output=True,
                text=True,
                timeout=GDAL2TILES_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode != 0:
                raise TileConversionError(
                    f"gdal2tiles.py failed (exit {result.returncode}): {result.stderr.strip()}"
                )


def convert_to_tile_pyramid(
    content: bytes,
    extension: str,
    out_dir: Path,
    *,
    builder: TilePyramidBuilder | None = None,
    max_zoom: int = DEFAULT_MAX_ZOOM,
) -> None:
    """Rasterizes `content` (bytes already fetched from the sanitized bucket) and
    writes an XYZ tile pyramid into `out_dir`. Raises TileConversionError on any
    failure — callers must catch it and report a terminal "conversion_failed" status
    rather than letting it propagate out of a Celery task (see app/sandbox/tasks.py)."""
    png_bytes = rasterize_to_png(content, extension)
    (builder or GdalRasterTilePyramidBuilder()).build(png_bytes, out_dir, max_zoom=max_zoom)
