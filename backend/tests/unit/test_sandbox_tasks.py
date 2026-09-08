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
    assert len(sent_tasks) == 1
    call = sent_tasks[0]
    assert call["name"] == "record_sandbox_result"
    assert call["queue"] == "sandbox-results"
    assert call["kwargs"]["status"] == "sanitized"


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
