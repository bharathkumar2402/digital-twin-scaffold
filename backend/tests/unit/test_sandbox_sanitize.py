"""Adversarial + happy-path tests for issue 2.2's pre-GDAL sanitization gate.

Written alongside the sanitizer itself, not after (per CLAUDE.md's extra-scrutiny
workflow for Phase 2 tasks 1-2) — the malicious-input cases below are the actual spec
for what `sanitize_upload` must reject, not a check bolted on afterward.
"""

import pytest

from app.sandbox.sanitize import (
    MAX_SANITIZE_BYTES,
    SanitizationError,
    sanitize_svg,
    sanitize_upload,
    validate_dxf,
    validate_pdf,
)

VALID_DXF = b"0\nSECTION\n2\nENTITIES\n0\nEOF\n"
VALID_PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n%%EOF"


# --- SVG: happy path -------------------------------------------------------------


def test_sanitize_svg_passes_through_clean_content() -> None:
    clean = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect x="0" y="0" width="10" height="10"/></svg>'
    )
    out = sanitize_svg(clean)
    assert b"<" in out and b"rect" in out


def test_sanitize_svg_preserves_local_fragment_hrefs() -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<defs><rect id="r1"/></defs>'
        b'<use xlink:href="#r1"/>'
        b"</svg>"
    )
    out = sanitize_svg(svg)
    assert b"#r1" in out


# --- SVG: adversarial cases -------------------------------------------------------


def test_sanitize_svg_strips_embedded_script_tag() -> None:
    malicious = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b"<script>alert(document.cookie)</script>"
        b"</svg>"
    )
    out = sanitize_svg(malicious)
    assert b"script" not in out.lower()
    assert b"alert" not in out


def test_sanitize_svg_strips_event_handler_attributes() -> None:
    malicious = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect onload="fetch(&apos;http://evil.com&apos;)" x="0"/>'
        b"</svg>"
    )
    out = sanitize_svg(malicious)
    assert b"onload" not in out.lower()
    assert b"evil.com" not in out


def test_sanitize_svg_strips_external_xlink_href() -> None:
    malicious = (
        b'<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<image xlink:href="http://evil.com/steal.png"/>'
        b"</svg>"
    )
    out = sanitize_svg(malicious)
    assert b"evil.com" not in out


def test_sanitize_svg_strips_foreign_object() -> None:
    malicious = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<foreignObject><body xmlns="http://www.w3.org/1999/xhtml">'
        b"<script>alert(1)</script></body></foreignObject>"
        b"</svg>"
    )
    out = sanitize_svg(malicious)
    assert b"foreignobject" not in out.lower()
    assert b"script" not in out.lower()


def test_sanitize_svg_rejects_xxe_doctype() -> None:
    xxe = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>'
    )
    with pytest.raises(SanitizationError):
        sanitize_svg(xxe)


def test_sanitize_svg_rejects_malformed_xml() -> None:
    with pytest.raises(SanitizationError):
        sanitize_svg(b"<svg><rect x=0></svg")


def test_sanitize_svg_rejects_non_svg_root() -> None:
    with pytest.raises(SanitizationError):
        sanitize_svg(b"<not-svg-at-all/>")


def test_sanitize_svg_rejects_empty_content() -> None:
    with pytest.raises(SanitizationError):
        sanitize_svg(b"")


def test_sanitize_svg_rejects_oversized_content() -> None:
    oversized = b'<svg xmlns="http://www.w3.org/2000/svg">' + (b" " * MAX_SANITIZE_BYTES)
    with pytest.raises(SanitizationError):
        sanitize_svg(oversized)


# --- DXF ---------------------------------------------------------------------------


def test_validate_dxf_accepts_well_formed_stub() -> None:
    validate_dxf(VALID_DXF)


def test_validate_dxf_rejects_corrupt_binary_garbage() -> None:
    with pytest.raises(SanitizationError):
        validate_dxf(bytes(range(256)))


def test_validate_dxf_rejects_non_ascii() -> None:
    with pytest.raises(SanitizationError):
        validate_dxf("0\nSECTION\n2\nENTITIES\n0\nEOF\nñ".encode())


def test_validate_dxf_rejects_missing_eof_sentinel() -> None:
    with pytest.raises(SanitizationError):
        validate_dxf(b"0\nSECTION\n2\nENTITIES\n")


def test_validate_dxf_rejects_non_numeric_group_codes() -> None:
    with pytest.raises(SanitizationError):
        validate_dxf(b"NOTACODE\nSECTION\n2\nENTITIES\n0\nEOF\n")


def test_validate_dxf_rejects_oversized_content() -> None:
    with pytest.raises(SanitizationError):
        validate_dxf(VALID_DXF + b" " * MAX_SANITIZE_BYTES)


# --- PDF ---------------------------------------------------------------------------


def test_validate_pdf_accepts_well_formed_stub() -> None:
    validate_pdf(VALID_PDF)


def test_validate_pdf_rejects_missing_header() -> None:
    with pytest.raises(SanitizationError):
        validate_pdf(b"not a pdf at all\n%%EOF")


def test_validate_pdf_rejects_missing_trailer() -> None:
    with pytest.raises(SanitizationError):
        validate_pdf(b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n")


def test_validate_pdf_rejects_embedded_javascript() -> None:
    with pytest.raises(SanitizationError):
        validate_pdf(b"%PDF-1.4\n<< /OpenAction << /JS (app.alert(1)) >> >>\n%%EOF")


def test_validate_pdf_rejects_oversized_content() -> None:
    with pytest.raises(SanitizationError):
        validate_pdf(VALID_PDF + b" " * MAX_SANITIZE_BYTES)


# --- dispatch ------------------------------------------------------------------


def test_sanitize_upload_dispatches_by_extension() -> None:
    assert sanitize_upload(VALID_DXF, "dxf") == VALID_DXF
    assert sanitize_upload(VALID_PDF, ".pdf") == VALID_PDF
    out = sanitize_upload(
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', "svg"
    )
    assert b"script" not in out.lower()


def test_sanitize_upload_rejects_unsupported_extension() -> None:
    with pytest.raises(SanitizationError):
        sanitize_upload(b"whatever", "exe")
