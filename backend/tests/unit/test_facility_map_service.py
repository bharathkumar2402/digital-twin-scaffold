from app.services.facility_map_service import ALLOWED_EXTENSIONS, _extension_of


def test_extension_of_lowercases_and_includes_dot() -> None:
    assert _extension_of("Plan.SVG") == ".svg"


def test_extension_of_handles_multiple_dots() -> None:
    assert _extension_of("floor.plan.v2.dxf") == ".dxf"


def test_extension_of_no_dot_is_empty_string() -> None:
    assert _extension_of("noextension") == ""


def test_extension_of_dotfile_with_no_suffix() -> None:
    # A bare dotfile like ".svg" should not be treated as a valid extension-only name.
    assert _extension_of(".svg") == ".svg"


def test_allowed_extensions_are_exactly_the_documented_formats() -> None:
    assert ALLOWED_EXTENSIONS == {".svg", ".dxf", ".pdf"}
