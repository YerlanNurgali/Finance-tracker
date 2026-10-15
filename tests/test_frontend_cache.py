"""The frontend has no versioned asset URLs, so every frontend file must be
revalidated by the browser instead of being reused from a heuristic cache."""
import pytest

import api

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.app.router, "on_startup", [])
    return TestClient(api.app)


@pytest.mark.parametrize("path", ["/", "/service-worker.js", "/static/script.js", "/static/i18n.js",
                                  "/static/style.css", "/static/manifest.json"])
def test_frontend_files_must_be_revalidated(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers.get("etag")


def test_revalidation_still_gets_not_modified(client):
    etag = client.get("/static/script.js").headers["etag"]
    response = client.get("/static/script.js", headers={"If-None-Match": etag})
    assert response.status_code == 304


def test_api_responses_are_not_affected(client):
    assert "cache-control" not in client.get("/health").headers
