import re
from xml.etree.ElementTree import tostring

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

# Duplicated from `app.core.config.Settings.max_map_upload_bytes` rather than imported —
# app/sandbox/** must never import anything from the main app (see
# tests/unit/test_sandbox_isolation.py), so this boundary is re-enforced independently
# here as defense-in-depth against a tampered/corrupted object in the raw-uploads bucket,
# not as the primary enforcement point (that's `facility_map_service.create_map_upload`).
MAX_SANITIZE_BYTES = 25 * 1024 * 1024

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_SVG_NS_SCRIPT = "{http://www.w3.org/2000/svg}script"
_SVG_NS_FOREIGN_OBJECT = "{http://www.w3.org/2000/svg}foreignObject"


class SanitizationError(Exception):
    """Raised for any malformed or actively-dangerous upload — never propagated as a
    silent pass-through. The sandbox task catches this and reports status=failed."""


def _check_size(content: bytes) -> None:
    if len(content) == 0:
        raise SanitizationError("empty file")
    if len(content) > MAX_SANITIZE_BYTES:
        raise SanitizationError(f"file exceeds {MAX_SANITIZE_BYTES}-byte limit")


def _strip_dangerous_svg_nodes(root: object) -> None:
    # ElementTree has no "remove all descendants matching X" primitive, so this walks
    # every element's direct children (the only place `Element.remove` can act) and
    # scrubs event-handler attributes and unsafe hrefs on every element along the way.
    for element in root.iter():  # type: ignore[attr-defined]
        for attr_name in [name for name in element.attrib if name.lower().startswith("on")]:
            del element.attrib[attr_name]

        for href_attr in ("href", _XLINK_HREF):
            href_value = element.attrib.get(href_attr)
            if href_value is not None and not href_value.startswith("#"):
                del element.attrib[href_attr]

    for parent in root.iter():  # type: ignore[attr-defined]
        for child in list(parent):
            tag = child.tag
            if tag in (_SVG_NS_SCRIPT, "script", _SVG_NS_FOREIGN_OBJECT, "foreignObject"):
                parent.remove(child)


def sanitize_svg(content: bytes) -> bytes:
    _check_size(content)
    try:
        root = fromstring(
            content,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise SanitizationError(f"malformed or unsafe SVG: {exc}") from exc

    if root.tag not in ("{http://www.w3.org/2000/svg}svg", "svg"):
        raise SanitizationError("root element is not <svg>")

    if root.tag in (_SVG_NS_SCRIPT, "script"):
        raise SanitizationError("root element is a <script> tag")

    _strip_dangerous_svg_nodes(root)

    return tostring(root, encoding="utf-8")


_DXF_GROUP_CODE_RE = re.compile(r"^\s*-?\d+\s*$")


def validate_dxf(content: bytes) -> None:
    _check_size(content)
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SanitizationError(f"DXF is not ASCII text: {exc}") from exc

    lines = [line for line in text.splitlines() if line.strip() != ""]
    if len(lines) < 4:
        raise SanitizationError("DXF file is too short to be valid")

    # DXF is a flat stream of alternating (group code, value) line pairs — verify that
    # shape holds for a leading window rather than fully parsing the file (GDAL, not
    # this sandbox, is responsible for real DXF parsing in task 2.3).
    window = lines[:200]
    for group_code_line in window[0::2]:
        if not _DXF_GROUP_CODE_RE.match(group_code_line):
            raise SanitizationError(
                f"DXF group code line is not numeric: {group_code_line!r}"
            )

    if "SECTION" not in text and "ENTITIES" not in text:
        raise SanitizationError("DXF file has no SECTION/ENTITIES content")
    if "EOF" not in text:
        raise SanitizationError("DXF file is missing an EOF sentinel")


_PDF_ACTIVE_CONTENT_TOKENS = (b"/JavaScript", b"/JS", b"/OpenAction", b"/AA")


def validate_pdf(content: bytes) -> None:
    _check_size(content)
    if not content.startswith(b"%PDF-"):
        raise SanitizationError("missing %PDF- header")
    if b"%%EOF" not in content[-2048:]:
        raise SanitizationError("missing %%EOF trailer")

    for token in _PDF_ACTIVE_CONTENT_TOKENS:
        if token in content:
            raise SanitizationError(f"PDF contains active-content token {token.decode()}")


def sanitize_upload(content: bytes, extension: str) -> bytes:
    """Dispatches by file extension (without the leading dot). Raises
    SanitizationError for anything malformed or actively dangerous; never returns
    unsanitized bytes for a format it doesn't recognize."""
    normalized = extension.lstrip(".").lower()
    if normalized == "svg":
        return sanitize_svg(content)
    if normalized == "dxf":
        validate_dxf(content)
        return content
    if normalized == "pdf":
        validate_pdf(content)
        return content
    raise SanitizationError(f"unsupported extension for sanitization: {extension!r}")
