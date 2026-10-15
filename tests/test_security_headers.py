"""Security headers on every response (errors included), no-store for the API,
API docs off in production, and a frontend that a strict CSP does not break.

No database is touched: data access is replaced by in-memory values, and the
production check runs in a subprocess with placeholder connection settings.
"""
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

import api
import database

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
        "connect-src 'self'; manifest-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.app.router, "on_startup", [])
    api.limiter.reset()
    yield TestClient(api.app, raise_server_exceptions=False)
    api.limiter.reset()


@pytest.fixture
def signed_in(client, monkeypatch):
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user,
                        lambda: {"id": 1, "email": "user@example.test"})
    monkeypatch.setattr(database, "get_operations",
                        lambda user_id: [(1, "01.10.2026, 00:00:00", "Доход", Decimal("10"), "Salary")])
    monkeypatch.setattr(database, "get_budgets", lambda user_id: [])
    monkeypatch.setattr(database, "get_goals", lambda user_id: [])
    monkeypatch.setattr(database, "get_user_settings",
                        lambda user_id: {"language": "RU", "theme": "light", "currency": "KZT"})
    return client


def assert_security_headers(response):
    for name, value in EXPECTED_HEADERS.items():
        assert response.headers.get(name) == value, f"{name} on {response.request.url.path} ({response.status_code})"


@pytest.mark.parametrize("method,path,status", [
    ("GET", "/", 200),
    ("GET", "/static/script.js", 200),
    ("GET", "/static/i18n.js", 200),
    ("GET", "/static/style.css", 200),
    ("GET", "/static/manifest.json", 200),
    ("GET", "/static/icons/icon-192.png", 200),
    ("GET", "/service-worker.js", 200),
    ("GET", "/health", 200),
    ("GET", "/operations", 401),
    ("POST", "/import/csv/preview", 401),
    ("GET", "/static/missing.js", 404),
    ("GET", "/no-such-page", 404),
    ("POST", "/auth/register", 422),
])
def test_every_response_carries_the_security_headers(client, method, path, status):
    response = client.request(method, path, json={} if method == "POST" else None)
    assert response.status_code == status
    assert_security_headers(response)


def test_an_unhandled_server_error_still_carries_the_headers(signed_in, monkeypatch):
    def broken(user_id):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(database, "get_operations", broken)
    response = signed_in.get("/operations")
    assert response.status_code == 500
    assert "database exploded" not in response.text
    assert_security_headers(response)
    assert response.headers["cache-control"] == "no-store"


def test_a_not_modified_answer_carries_the_headers(client):
    etag = client.get("/static/script.js").headers["etag"]
    response = client.get("/static/script.js", headers={"If-None-Match": etag})
    assert response.status_code == 304
    assert_security_headers(response)


def test_the_policy_is_strict():
    policy = api.CONTENT_SECURITY_POLICY
    for forbidden in ("unsafe-inline", "unsafe-eval", "unsafe-hashes", "*", "data:", "blob:", "http:", "https:", "nonce-"):
        assert forbidden not in policy, forbidden
    for directive in ("frame-ancestors 'none'", "object-src 'none'", "base-uri 'none'", "default-src 'self'"):
        assert directive in policy


@pytest.mark.parametrize("path", ["/operations", "/operations?period=month", "/balance", "/statistics",
                                  "/budgets", "/goals", "/auth/me", "/export/csv"])
def test_private_api_responses_are_never_stored(signed_in, path):
    response = signed_in.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_refused_api_requests_are_never_stored_either(client):
    assert client.get("/operations").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/", "/static/script.js", "/static/style.css", "/service-worker.js"])
def test_frontend_files_keep_revalidation(client, path):
    response = client.get(path)
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers.get("etag")


def test_import_uploads_still_stream_through_the_middleware(signed_in, monkeypatch):
    monkeypatch.setattr(database, "find_imported_hashes", lambda user_id, hashes: set())

    def chunks():
        yield "Дата;Сумма\n".encode()
        yield "15.01.2026;-100\n".encode()

    response = signed_in.post("/import/csv/preview?date_column=0&amount_column=1", content=chunks(),
                              headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    assert response.json()["valid_rows"] == 1
    assert_security_headers(response)


# --- API documentation ----------------------------------------------------------

def test_docs_are_available_locally_with_the_other_headers(client):
    assert api.API_DOCS_ENABLED, "tests run with the local configuration"
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_only_the_interactive_docs_pages_skip_the_policy(client, path):
    # Swagger UI needs an inline script and a CDN, ReDoc an inline style, a CDN
    # and web fonts: these two local pages cannot work under the policy.
    assert "content-security-policy" not in client.get(path).headers


@pytest.mark.parametrize("path", ["/openapi.json", "/docs/oauth2-redirect"])
def test_other_docs_resources_keep_the_policy(client, path):
    # /openapi.json is JSON; the OAuth2 redirect page serves OAuth2 sign-in only,
    # and this API has no OAuth2 scheme, so neither needs an exception.
    assert client.get(path).headers["content-security-policy"] == EXPECTED_HEADERS["content-security-policy"]
    assert "securitySchemes" not in api.app.openapi().get("components", {}), "no OAuth2 sign-in to redirect to"


def _docs_statuses(env):
    script = (
        "import json, sys; sys.path.insert(0, '.');"
        "import api; from fastapi.testclient import TestClient;"
        "api.app.router.on_startup.clear();"
        "c = TestClient(api.app, raise_server_exceptions=False);"
        "r = {p: c.get(p) for p in ('/docs', '/redoc', '/openapi.json', '/docs/oauth2-redirect')};"
        "print(json.dumps({'enabled': api.API_DOCS_ENABLED,"
        " 'status': {p: x.status_code for p, x in r.items()},"
        " 'csp': all('content-security-policy' in x.headers for x in r.values())}))"
    )
    base = {key: value for key, value in os.environ.items() if key not in ("RENDER", "API_DOCS")}
    # Placeholders: load_dotenv() never overrides set variables, so .env is not read.
    base.update({"DATABASE_URL": "postgresql://tests-not-used@127.0.0.1:1/none", "SECRET_KEY": "tests",
                 "PYTHONDONTWRITEBYTECODE": "1"})
    base.update(env)
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=base, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_docs_are_off_in_production():
    production = _docs_statuses({"RENDER": "true"})
    assert production["enabled"] is False
    assert production["status"] == {"/docs": 404, "/redoc": 404, "/openapi.json": 404, "/docs/oauth2-redirect": 404}
    assert production["csp"], "the 404 answers carry the policy"


@pytest.mark.parametrize("env,enabled", [
    ({}, True),
    ({"API_DOCS": "0"}, False),
    ({"RENDER": "true", "API_DOCS": "1"}, True),
])
def test_api_docs_setting(env, enabled):
    assert _docs_statuses(env)["enabled"] is enabled


# --- The frontend itself must not need inline code --------------------------------

def test_index_html_has_no_inline_styles_scripts_or_handlers():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"\sstyle\s*=", html, re.I), "style attribute"
    assert "<style" not in html.lower()
    assert not re.search(r"\son[a-z]+\s*=", html, re.I), "inline event handler"
    assert "javascript:" not in html.lower()
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.I | re.S)
    assert scripts and all('src="/static/' in attrs and not body.strip() for attrs, body in scripts)


def test_frontend_scripts_do_not_evaluate_strings():
    for name in ("script.js", "i18n.js", "service-worker.js"):
        source = (FRONTEND / name).read_text(encoding="utf-8")
        assert not re.search(r"\beval\s*\(|new\s+Function\s*\(|set(Timeout|Interval)\(\s*[\"'`]", source), name


def test_former_inline_styles_live_in_the_stylesheet():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    assert re.search(r'id="delete-account-btn" class="[^"]*\bdanger-button\b', html)
    assert 'class="form-field danger-zone-field"' in html
    assert ".secondary-button.danger-button:hover" in css
    assert ".danger-zone-field { margin-bottom: 12px; }" in css


# --- Unhandled exceptions at the ASGI level -------------------------------------
# These call the real ASGI application directly and record every message it
# sends, so a second http.response.start or a leaked exception text would show.

import asyncio  # noqa: E402

from starlette.middleware.errors import ServerErrorMiddleware  # noqa: E402

SECRET = "connection to ep-secret-host failed: password=hunter2"


def asgi_request(app, method, path, body_chunks=(b"",), headers=()):
    sent, pending = [], list(body_chunks)

    async def receive():
        chunk = pending.pop(0) if pending else b""
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(name.encode(), value.encode()) for name, value in headers],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    error = None
    try:
        asyncio.run(app(scope, receive, send))
    except Exception as exc:  # ServerErrorMiddleware re-raises for the server log
        error = exc
    return sent, error


def starts(sent):
    return [message for message in sent if message["type"] == "http.response.start"]


def header_map(start):
    return {name.decode().lower(): value.decode() for name, value in start["headers"]}


def test_an_unhandled_exception_yields_exactly_one_500_with_the_headers(monkeypatch):
    def broken(user_id):
        raise RuntimeError(SECRET)

    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user, lambda: {"id": 1, "email": "u@example.test"})
    monkeypatch.setattr(database, "get_operations", broken)
    sent, error = asgi_request(api.app, "GET", "/operations")

    assert isinstance(error, RuntimeError), "the exception still reaches the server log"
    (start,) = starts(sent)
    assert start["status"] == 500
    headers = header_map(start)
    for name, value in EXPECTED_HEADERS.items():
        assert headers[name] == value, name
    assert headers["cache-control"] == "no-store"
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    assert body == b"Internal Server Error"
    assert b"hunter2" not in body and b"ep-secret-host" not in body


def test_an_exception_after_the_response_started_sends_no_second_start():
    async def streams_then_fails(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/csv")]})
        await send({"type": "http.response.body", "body": b"ID,Amount\n", "more_body": True})
        raise RuntimeError(SECRET)

    app = ServerErrorMiddleware(api.SecurityHeadersMiddleware(streams_then_fails), handler=api.internal_server_error)
    sent, error = asgi_request(app, "GET", "/export/csv")

    assert isinstance(error, RuntimeError)
    (start,) = starts(sent)
    assert start["status"] == 200
    assert header_map(start)["content-security-policy"] == EXPECTED_HEADERS["content-security-policy"]
    assert not any(SECRET.encode() in message.get("body", b"") for message in sent)


def test_csv_upload_streams_in_chunks_through_the_full_stack(monkeypatch):
    monkeypatch.setitem(api.app.dependency_overrides, api.get_current_user, lambda: {"id": 1, "email": "u@example.test"})
    monkeypatch.setattr(database, "get_user_settings", lambda user_id: {"currency": "KZT"})
    monkeypatch.setattr(database, "find_imported_hashes", lambda user_id, hashes: set())
    api.limiter.reset()
    chunks = ("Дата;Сумма\n".encode(), "15.01.2026;-100\n".encode(), "16.01.2026;200\n".encode())
    sent, error = asgi_request(api.app, "POST", "/import/csv/preview", body_chunks=chunks,
                               headers=[("content-type", "text/csv")])
    assert error is None
    (start,) = starts(sent)
    assert start["status"] == 200
    assert header_map(start)["cache-control"] == "no-store"
    body = json.loads(b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"))
    assert body["total_rows"] == 2, "every chunk of the upload was read"


def test_the_app_never_runs_in_debug_mode():
    # With debug=True, Starlette's ServerErrorMiddleware ignores the Exception
    # handler above and answers an unhandled error with its own traceback page:
    # the exception text (which may name the database host or credentials)
    # reaches the client, and the response carries none of the security headers
    # or no-store. Debug mode must therefore never be switched on.
    assert api.app.debug is False


# --- Token signing key ------------------------------------------------------------

def _reload_auth(monkeypatch, value):
    import importlib
    import auth
    if value is None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY", value)
    reloaded = importlib.reload(auth)
    return reloaded


@pytest.fixture
def restore_auth():
    import importlib
    import auth
    original = auth.SECRET_KEY, auth.SECRET_KEY_CONFIGURED
    yield
    importlib.reload(auth)
    auth.SECRET_KEY, auth.SECRET_KEY_CONFIGURED = original


@pytest.mark.parametrize("value", [None, ""])
def test_a_missing_or_empty_key_is_never_used_to_sign(monkeypatch, restore_auth, value):
    auth = _reload_auth(monkeypatch, value)
    assert auth.SECRET_KEY_CONFIGURED is False
    assert len(auth.SECRET_KEY) == 64, "a random key is used instead of an empty one"


@pytest.mark.parametrize("value,warning", [
    (None, "SECRET_KEY не задан"), ("sh0rt-K3y-marker", "SECRET_KEY короче 32 символов"), ("k3y-" * 16, None),
])
def test_startup_warns_about_the_signing_key_without_revealing_it(monkeypatch, restore_auth, caplog, value, warning):
    _reload_auth(monkeypatch, value)
    api.warn_about_configuration()
    if warning:
        assert warning in caplog.text
    else:
        assert "SECRET_KEY" not in caplog.text
    if value:
        assert value not in caplog.text
