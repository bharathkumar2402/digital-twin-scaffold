"""Unit tests for the CORS middleware added in issue 2.5.

A CORS preflight (OPTIONS) is handled entirely by Starlette's CORSMiddleware before any
route or dependency runs, so this needs no database at all - a plain TestClient against
the real app is enough, unlike the DB-backed integration tests in tests/integration/.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_preflight_allows_the_configured_frontend_origin() -> None:
    client = TestClient(app)
    response = client.options(
        "/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_preflight_rejects_an_unlisted_origin() -> None:
    client = TestClient(app)
    response = client.options(
        "/login",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers
