import re

import pytest

from site_app import auth, db


PROTECTED_ROUTES = (
    "/login", "/login2", "/logout", "/settings", "/settings/test-email",
    "/settings/test-telegram", "/settings/api-key/generate", "/settings/api-key/revoke",
)


@pytest.mark.parametrize("path", PROTECTED_ROUTES)
def test_missing_token_rejected(client, path):
    assert client.post(path).status_code == 400


@pytest.mark.parametrize("path", PROTECTED_ROUTES)
def test_valid_token_accepted(client, login, csrf_post, path):
    login()
    assert csrf_post(path).status_code != 400


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("token", [None, "wrong", "неверный"])
def test_invalid_token_rejected(client, csrf_token, method, token):
    csrf_token()
    data = {} if token is None else {"csrf_token": token}
    assert client.open("/login", method=method, data=data).status_code == 400


def test_form_token_without_session_rejected(client):
    assert client.post("/login", data={"csrf_token": "untrusted"}).status_code == 400


def test_api_ping_without_csrf(client):
    key = db.generate_api_key()
    assert client.get("/api/v1/ping", headers={"X-API-Key": key}).json == {"ok": True}
    with client.session_transaction() as sess:
        assert "csrf_token" not in sess


def test_api_mutation_exempt(app, client):
    app.add_url_rule("/api/v1/csrf-probe", endpoint="api.csrf_probe", view_func=lambda: "ok", methods=["POST"])
    assert client.post("/api/v1/csrf-probe").status_code == 200


def test_api_endpoint_outside_api_prefix_requires_csrf(app, client):
    app.add_url_rule("/not-api/probe", endpoint="api.csrf_probe", view_func=lambda: "ok", methods=["POST"])
    assert client.post("/not-api/probe").status_code == 400


def test_login_rotates_csrf_token(client):
    first_page = client.get("/login")
    first_token = re.search(r'name="csrf_token" value="([^"]+)"', first_page.text).group(1)
    assert client.post("/login", data={
        "username": "admin", "password": "first-password", "csrf_token": first_token,
    }).location == "/login2"
    second_page = client.get("/login2")
    tokens = re.findall(r'name="csrf_token" value="([^"]+)"', second_page.text)
    assert len(tokens) == 2
    assert tokens[0] == tokens[1] != first_token
    with client.session_transaction() as sess:
        assert sess["csrf_token"] == tokens[0]
    assert client.post("/login2", data={
        "password": "second-password", "csrf_token": first_token,
    }).status_code == 400
    assert client.post("/login2", data={
        "password": "second-password", "csrf_token": tokens[0],
    }).location == "/"
    with client.session_transaction() as sess:
        assert sess["stage"] == auth.STAGE_FULL
