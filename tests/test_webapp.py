import os
import re
from datetime import timedelta

import pytest
from flask import request

from site_app import auth
from site_app.auth_views import _BAD_CREDENTIALS, _BAD_SECOND_PASSWORD, _RATE_LIMITED
from site_app.webapp import ROBOTS_TXT, _REQUIRED_ENV, create_app


def test_full_flow(client, first_factor, csrf_post):
    assert client.get("/").location == "/login"
    first = first_factor(follow_redirects=True)
    assert first.request.path == "/login2"
    assert "Шаг 2 из 2" in first.text
    assert client.get("/").location == "/login"
    response = csrf_post("/login2", data={"password": "second-password"}, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/"
    assert "Добро пожаловать" in response.text
    assert "Выйти" in response.text
    with client.session_transaction() as session:
        assert session["stage"] == auth.STAGE_FULL
        assert session.permanent
    for path in ("/login", "/login2"):
        assert client.get(path).location == "/"
    assert client.get("/logout").status_code == 405
    assert csrf_post("/logout").location == "/login"
    assert client.get("/").location == "/login"
    with client.session_transaction() as session:
        assert not session


@pytest.mark.parametrize("method", ["get", "post"])
def test_second_factor_requires_first(client, method, csrf_post):
    assert (csrf_post("/login2") if method == "post" else client.get("/login2")).location == "/login"


@pytest.mark.parametrize("data", [{}, {"username": "admin", "password": "wrong"}, {"username": "wrong", "password": "first-password"}])
def test_bad_first_factor(client, data, csrf_post):
    response = csrf_post("/login", data=data)
    assert response.status_code == 401
    assert _BAD_CREDENTIALS in response.text
    assert "auth-error" in response.text


def test_bad_second_factor(client, first_factor, csrf_post):
    first_factor()
    response = csrf_post("/login2", data={"password": "wrong"})
    assert response.status_code == 401
    assert _BAD_SECOND_PASSWORD in response.text
    assert client.get("/").location == "/login"


def test_first_factor_clears_stale_session_and_trims_username(client, csrf_post):
    with client.session_transaction() as session:
        session["stale"] = True
    assert csrf_post("/login", data={"username": " admin ", "password": "first-password"}).location == "/login2"
    with client.session_transaction() as session:
        assert dict(session) == {"stage": auth.STAGE_FIRST}


@pytest.mark.parametrize("path", ["/login", "/login2"])
def test_rate_limit_after_five_failures(client, first_factor, path, csrf_post):
    if path == "/login2":
        first_factor()
    for _ in range(5):
        assert csrf_post(path, data={"username": "admin", "password": "wrong"}).status_code == 401
    response = csrf_post(path, data={"password": "wrong"})
    assert response.status_code == 429
    assert _RATE_LIMITED in response.text


def test_first_factor_does_not_reset_shared_limit(client, first_factor, csrf_post):
    first_factor()
    for _ in range(4):
        assert csrf_post("/login2", data={"password": "wrong"}).status_code == 401
    assert first_factor().status_code == 302
    assert csrf_post("/login2", data={"password": "wrong"}).status_code == 401
    assert first_factor().status_code == 429
    assert csrf_post("/login2", data={"password": "second-password"}).status_code == 429


def test_full_login_clears_failures(client, login, csrf_post):
    for _ in range(4):
        csrf_post("/login", data={"username": "admin", "password": "wrong"})
    assert login().location == "/"
    assert not auth._attempts


def test_forwarded_spoof_does_not_reset_limit(client, csrf_post):
    for i in range(5):
        response = csrf_post("/login", headers={"X-Forwarded-For": f"spoof-{i}, 10.0.0.5"})
        assert response.status_code == 401
    assert csrf_post("/login", headers={"X-Forwarded-For": "new-spoof, 10.0.0.5"}).status_code == 429
    assert csrf_post("/login", headers={"X-Forwarded-For": "10.0.0.6"}).status_code == 401


def test_healthz_public(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.text == "ok"


def test_robots_txt_public(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/plain")
    assert response.data == ROBOTS_TXT.encode("utf-8")


@pytest.mark.parametrize("stage", [None, auth.STAGE_FIRST, auth.STAGE_FULL])
@pytest.mark.parametrize("path", ["/", "/login", "/login2"])
def test_sensitive_pages_are_not_stored(client, stage, path):
    with client.session_transaction() as session:
        if stage is not None:
            session["stage"] = stage
    response = client.get(path)
    assert response.status_code in (200, 302)
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/login", "/login2"])
@pytest.mark.parametrize("result", ["success", "invalid", "limited"])
def test_login_submissions_are_not_stored(client, path, result, csrf_post):
    with client.session_transaction() as session:
        session["stage"] = auth.STAGE_FIRST
    if result == "limited":
        for _ in range(auth._MAX_ATTEMPTS):
            csrf_post(path, data={"password": "wrong"})
    password = "first-password" if path == "/login" else "second-password"
    response = csrf_post(path, data={
        "username": "admin",
        "password": password if result == "success" else "wrong",
    })
    assert response.status_code == {"success": 302, "invalid": 401, "limited": 429}[result]
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/healthz", "/static/style.css"])
def test_public_resources_keep_cache_policy(client, path):
    with client.get(path) as response:
        assert response.status_code == 200
        assert not response.cache_control.no_store


def test_login_required_prevents_caching_error_responses(app, client):
    from flask import abort

    @app.get("/protected-error")
    @auth.login_required
    def protected_error():
        abort(403)

    with client.session_transaction() as session:
        session["stage"] = auth.STAGE_FULL
    response = client.get("/protected-error")
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("testing", [False, True])
def test_all_missing_variables_are_listed(testing):
    with pytest.raises(RuntimeError) as error:
        create_app({"TESTING": testing})
    for name in _REQUIRED_ENV:
        assert name in str(error.value)


def test_partial_config_lists_only_missing(credentials):
    with pytest.raises(RuntimeError) as error:
        create_app({"SECRET_KEY": credentials["SECRET_KEY"]})
    assert "SECRET_KEY" not in str(error.value)
    for name in _REQUIRED_ENV[1:]:
        assert name in str(error.value)


def test_environment_and_config_override(monkeypatch, credentials):
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    app = create_app({"SECRET_KEY": "override"})
    assert app.config["SECRET_KEY"] == "override"
    assert app.config["AUTH_USERNAME"] == credentials["AUTH_USERNAME"]
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.permanent_session_lifetime == timedelta(hours=12)


@pytest.mark.parametrize("value, expected", [(None, True), ("0", False), ("1", True), ("false", True), ("", True)])
def test_secure_cookie_environment(monkeypatch, credentials, value, expected):
    if value is not None:
        monkeypatch.setenv("SESSION_COOKIE_SECURE", value)
    assert create_app(credentials).config["SESSION_COOKIE_SECURE"] is expected


def test_cookie_flags(credentials, csrf_post):
    client = create_app(credentials).test_client()
    response = csrf_post("/login", target=client, data={"username": "admin", "password": "first-password"})
    cookie = response.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_one_trusted_proxy(app, client):
    @app.get("/proxy-test")
    def proxy_test():
        return {"address": request.remote_addr, "scheme": request.scheme, "host": request.host}
    response = client.get("/proxy-test", headers={
        "X-Forwarded-For": "198.51.100.1, 203.0.113.2",
        "X-Forwarded-Proto": "http, https",
        "X-Forwarded-Host": "untrusted.example",
    })
    assert response.json == {"address": "203.0.113.2", "scheme": "https", "host": "localhost"}


def test_login_render_and_static_assets(client):
    html = client.get("/login").text
    assert "Шаг 1 из 2" in html
    assert 'name="username"' in html and 'name="password"' in html
    assert 'action="/login"' in html
    assert "venom_shadows site" in html
    paths = re.findall(r"/static/([^?'\"]+)\?v=([0-9]+)", html)
    assert {path for path, _ in paths} >= {"style.css", "img/logo.webp", "img/clouds.svg", "img/rain.svg"}
    for path, version in paths:
        assert int(version) > 0
        with client.get("/static/" + path) as response:
            assert response.status_code == 200
    with client.get("/static/style.css") as response:
        assert "var(--clouds)" in response.text and "var(--rain)" in response.text
        assert "clouds.svg" not in response.text and "rain.svg" not in response.text


def test_static_url_nanoseconds_and_missing_file(app, tmp_path):
    app.static_folder = str(tmp_path)
    path = tmp_path / "a.css"
    path.write_text("x")
    static_url = app.jinja_env.globals["static_url"]
    with app.test_request_context():
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        before = static_url("a.css")
        os.utime(path, ns=(1_001_000_000, 1_001_000_000))
        assert static_url("a.css") != before
        assert static_url("missing.css").endswith("?v=0")
