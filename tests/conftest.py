import secrets

import pytest
from werkzeug.security import generate_password_hash

from site_app import auth
from site_app.webapp import _REQUIRED_ENV, create_app


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "site.db"))
    for name in (*_REQUIRED_ENV, "SESSION_COOKIE_SECURE"):
        monkeypatch.delenv(name, raising=False)
    with auth._attempts_lock:
        auth._attempts.clear()
    yield
    with auth._attempts_lock:
        auth._attempts.clear()


@pytest.fixture(scope="session")
def credentials():
    return {
        "SECRET_KEY": "test-secret",
        "AUTH_USERNAME": "admin",
        "AUTH_PASSWORD_HASH": generate_password_hash("first-password"),
        "AUTH_SECOND_PASSWORD_HASH": generate_password_hash("second-password"),
    }


@pytest.fixture
def app(credentials):
    return create_app({**credentials, "TESTING": True, "SESSION_COOKIE_SECURE": False})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def first_factor(csrf_post):
    def submit(**kwargs):
        return csrf_post("/login", data={"username": "admin", "password": "first-password"}, **kwargs)
    return submit


@pytest.fixture
def login(csrf_post, first_factor):
    def submit():
        first_factor()
        return csrf_post("/login2", data={"password": "second-password"})
    return submit


@pytest.fixture
def csrf_token(client):
    def set_token(target=None):
        token = secrets.token_urlsafe(32)
        with (target or client).session_transaction() as sess:
            sess["csrf_token"] = token
        return token
    return set_token


@pytest.fixture
def csrf_post(client, csrf_token):
    def submit(path, data=None, target=None, **kwargs):
        target = target or client
        return target.post(path, data={**(data or {}), "csrf_token": csrf_token(target)}, **kwargs)
    return submit
