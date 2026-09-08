"""Task-level tests for issue 2.2: `receive_map_upload` must report status=failed for
a rejected file rather than raising, and status=sanitized for a clean one — either way
without ever writing to Postgres itself (it can't; see test_sandbox_isolation.py)."""

from typing import Any

import pytest

from app.sandbox import tasks


@pytest.fixture
def sent_tasks(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_send_task(name: str, kwargs: dict[str, Any], queue: str) -> None:
        calls.append({"name": name, "kwargs": kwargs, "queue": queue})

    monkeypatch.setattr(tasks.sandbox_celery_app, "send_task", fake_send_task)
    return calls


def test_receive_map_upload_reports_sanitized_for_clean_svg(
    monkeypatch: pytest.MonkeyPatch, sent_tasks: list[dict[str, Any]]
) -> None:
    clean_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect x="0"/></svg>'
    monkeypatch.setattr(tasks, "fetch_raw_upload", lambda storage_key: clean_svg)
    put_calls: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        tasks, "put_sanitized_upload", lambda key, content: put_calls.append((key, content))
    )

    tasks.receive_map_upload("upload-1", "tenant-1", "tenant-1/facility-1/upload-1.svg")

    assert len(put_calls) == 1
    assert len(sent_tasks) == 2
    result_call = sent_tasks[0]
    assert result_call["name"] == "record_sandbox_result"
    assert result_call["queue"] == "sandbox-results"
    assert result_call["kwargs"]["status"] == "sanitized"

    convert_call = sent_tasks[1]
    assert convert_call["name"] == "convert_sanitized_map"
    assert convert_call["queue"] == "upload-sandbox"
    assert convert_call["kwargs"] == {
        "upload_id": "upload-1",
        "tenant_id": "tenant-1",
        "storage_key": "tenant-1/facility-1/upload-1.svg",
    }


def test_receive_map_upload_reports_failed_for_malicious_svg_without_raising(
    monkeypatch: pytest.MonkeyPatch, sent_tasks: list[dict[str, Any]]
) -> None:
    malicious_svg = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>'
    )
    monkeypatch.setattr(tasks, "fetch_raw_upload", lambda storage_key: malicious_svg)
    put_calls: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        tasks, "put_sanitized_upload", lambda key, content: put_calls.append((key, content))
    )

    # Must not raise — a raised exception would leave the row stuck at PROCESSING
    # forever instead of reporting the terminal "failed" status.
    tasks.receive_map_upload("upload-2", "tenant-1", "tenant-1/facility-1/upload-2.svg")

    assert put_calls == []
    assert len(sent_tasks) == 1
    call = sent_tasks[0]
    assert call["kwargs"]["status"] == "failed"
    assert call["kwargs"]["detail"]


def test_receive_map_upload_reports_failed_for_corrupt_dxf(
    monkeypatch: pytest.MonkeyPatch, sent_tasks: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(tasks, "fetch_raw_upload", lambda storage_key: b"garbage, not dxf")
    monkeypatch.setattr(
        tasks, "put_sanitized_upload", lambda key, content: pytest.fail("should not be called")
    )

    tasks.receive_map_upload("upload-3", "tenant-1", "tenant-1/facility-1/upload-3.dxf")

    assert sent_tasks[0]["kwargs"]["status"] == "failed"


def test_extension_of_handles_no_extension() -> None:
    assert tasks._extension_of("no-extension-here") == ""
    assert tasks._extension_of("path/to/file.svg") == "svg"


def test_tile_prefix_of_strips_extension() -> None:
    assert tasks._tile_prefix_of("tenant-1/facility-1/upload-1.svg") == (
        "tenant-1/facility-1/upload-1"
    )
    assert tasks._tile_prefix_of("no-extension-here") == "no-extension-here"


def test_convert_sanitized_map_reports_tiled_on_success(
    monkeypatch: pytest.MonkeyPatch, sent_tasks: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(tasks, "fetch_sanitized_upload", lambda storage_key: b"<svg/>")

    def fake_convert(content: bytes, extension: str, out_dir: Any, **kwargs: Any) -> None:
        assert content == b"<svg/>"
        assert extension == "svg"
        (out_dir / "0" / "0" / "0.png").parent.mkdir(parents=True)
        (out_dir / "0" / "0" / "0.png").write_bytes(b"fake-tile")

    monkeypatch.setattr(tasks, "convert_to_tile_pyramid", fake_convert)
    put_calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        tasks, "put_tile_pyramid", lambda prefix, tile_dir: put_calls.append((prefix, tile_dir))
    )

    tasks.convert_sanitized_map("upload-1", "tenant-1", "tenant-1/facility-1/upload-1.svg")

    assert len(put_calls) == 1
    assert put_calls[0][0] == "tenant-1/facility-1/upload-1"
    assert len(sent_tasks) == 1
    call = sent_tasks[0]
    assert call["kwargs"]["status"] == "tiled"
    assert call["kwargs"]["tile_prefix"] == "tenant-1/facility-1/upload-1"


def test_convert_sanitized_map_reports_conversion_failed_without_raising(
    monkeypatch: pytest.MonkeyPatch, sent_tasks: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(tasks, "fetch_sanitized_upload", lambda storage_key: b"garbage")

    def failing_convert(content: bytes, extension: str, out_dir: Any, **kwargs: Any) -> None:
        raise tasks.TileConversionError("no rasterizer for extension 'xyz'")

    monkeypatch.setattr(tasks, "convert_to_tile_pyramid", failing_convert)
    monkeypatch.setattr(
        tasks, "put_tile_pyramid", lambda prefix, tile_dir: pytest.fail("should not be called")
    )

    # Must not raise — same reasoning as receive_map_upload's failure path.
    tasks.convert_sanitized_map("upload-2", "tenant-1", "tenant-1/facility-1/upload-2.xyz")

    assert len(sent_tasks) == 1
    call = sent_tasks[0]
    assert call["kwargs"]["status"] == "conversion_failed"
    assert call["kwargs"]["detail"]
    assert "tile_prefix" not in call["kwargs"]
