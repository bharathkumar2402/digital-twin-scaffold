"""Unit tests for issue 2.3's GDAL conversion pipeline (app/sandbox/convert.py).

This module is importable on hosts without GDAL, cairo, or the other native libs the
sandbox Docker image actually ships (see convert.py's module docstring) because every
such dependency is imported lazily inside the function that uses it. These tests
exploit exactly that: they monkeypatch RASTERIZERS entries and inject a fake
TilePyramidBuilder, so no real cairosvg/ezdxf/GDAL call ever happens here. The real
calls are only exercised by actually running the sandbox container.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.sandbox import convert


def test_rasterize_to_png_dispatches_by_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(convert.RASTERIZERS, "svg", lambda content: b"svg-png:" + content)

    result = convert.rasterize_to_png(b"<svg/>", "SVG")

    assert result == b"svg-png:<svg/>"


def test_rasterize_to_png_rejects_unsupported_extension() -> None:
    with pytest.raises(convert.TileConversionError, match="no rasterizer"):
        convert.rasterize_to_png(b"whatever", "xyz")


class _FakeBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, Path, int]] = []

    def build(self, png_bytes: bytes, out_dir: Path, *, max_zoom: int) -> None:
        self.calls.append((png_bytes, out_dir, max_zoom))


def test_convert_to_tile_pyramid_rasterizes_then_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(convert.RASTERIZERS, "svg", lambda content: b"rasterized")
    builder = _FakeBuilder()

    convert.convert_to_tile_pyramid(b"<svg/>", "svg", tmp_path, builder=builder)

    assert builder.calls == [(b"rasterized", tmp_path, convert.DEFAULT_MAX_ZOOM)]


def test_convert_to_tile_pyramid_propagates_rasterization_failure(tmp_path: Path) -> None:
    with pytest.raises(convert.TileConversionError):
        convert.convert_to_tile_pyramid(
            b"whatever", "unsupported", tmp_path, builder=_FakeBuilder()
        )


def test_gdal_raster_tile_pyramid_builder_invokes_raster_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        source_path = Path(args[-2])
        assert source_path.read_bytes() == b"fake-png-bytes"
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    convert.GdalRasterTilePyramidBuilder().build(b"fake-png-bytes", tmp_path, max_zoom=6)

    args = captured["args"]
    assert args[0] == "gdal2tiles.py"
    assert "-p" in args and args[args.index("-p") + 1] == "raster"
    assert "-z" in args and args[args.index("-z") + 1] == "0-6"
    assert args[-1] == str(tmp_path)


def test_gdal_raster_tile_pyramid_builder_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(convert.TileConversionError, match="boom"):
        convert.GdalRasterTilePyramidBuilder().build(b"fake-png-bytes", tmp_path)
