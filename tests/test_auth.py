import concurrent.futures

import pytest

from site_app import auth


class TestFactorChecks:
    def test_first_factor_accepts_correct_credentials(self, app):
        with app.test_request_context():
            assert (auth.check_first_factor("admin", "first-password"))

    def test_first_factor_rejects_wrong_password(self, app):
        with app.test_request_context():
            assert not (auth.check_first_factor("admin", "wrong"))

    def test_first_factor_rejects_wrong_username(self, app):
        with app.test_request_context():
            assert not (auth.check_first_factor("someone", "first-password"))

    def test_first_factor_rejects_empty_input(self, app):
        with app.test_request_context():
            assert not (auth.check_first_factor("", ""))

    def test_first_factor_rejects_second_password(self, app):
        with app.test_request_context():
            assert not (auth.check_first_factor("admin", "second-password"))

    def test_second_factor_accepts_correct_password(self, app):
        with app.test_request_context():
            assert (auth.check_second_factor("second-password"))

    def test_second_factor_rejects_wrong_password(self, app):
        with app.test_request_context():
            assert not (auth.check_second_factor("wrong"))

    def test_second_factor_rejects_first_password(self, app):
        with app.test_request_context():
            assert not (auth.check_second_factor("first-password"))

    def test_second_factor_rejects_empty_password(self, app):
        with app.test_request_context():
            assert not (auth.check_second_factor(""))


class TestRateLimiting:
    def test_not_rate_limited_below_threshold(self, app):
        with app.test_request_context():
            for _ in range(auth._MAX_ATTEMPTS - 1):
                assert auth.reserve_attempt()
            assert auth.reserve_attempt()

    def test_rate_limited_at_threshold(self, app):
        with app.test_request_context():
            for _ in range(auth._MAX_ATTEMPTS):
                assert auth.reserve_attempt()
            assert not auth.reserve_attempt()

    def test_clear_attempts_resets_limit(self, app):
        with app.test_request_context():
            for _ in range(auth._MAX_ATTEMPTS):
                assert auth.reserve_attempt()
            auth.clear_attempts()
            assert auth.reserve_attempt()

    def test_attempts_expire_after_window(self, app):
        with app.test_request_context():
            for _ in range(auth._MAX_ATTEMPTS):
                assert auth.reserve_attempt()
            # Сдвигаем записанные попытки за пределы окна вместо ожидания
            # реальных пяти минут.
            key = auth._client_key()
            expired = [t - auth._WINDOW_SECONDS - 1 for t in auth._attempts[key]]
            auth._attempts[key] = expired
            assert auth.reserve_attempt()

    def test_expired_attempts_do_not_leak_keys(self, app):
        """Ключи протухших попыток не должны накапливаться в памяти."""
        with app.test_request_context():
            assert auth.reserve_attempt()
            key = auth._client_key()
            auth._attempts[key] = [auth._attempts[key][0] - auth._WINDOW_SECONDS - 1]
            auth.release_attempt()
            assert key not in auth._attempts

    def test_limit_is_per_client_address(self, app):
        with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.1"}):
            for _ in range(auth._MAX_ATTEMPTS):
                assert auth.reserve_attempt()
            assert not auth.reserve_attempt()
        with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"}):
            assert auth.reserve_attempt()



    def test_reserve_attempt_race_is_atomic(self, app):
        def attempt(_):
            with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.9"}):
                return auth.reserve_attempt()
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(attempt, range(20)))
        assert results.count(True) == auth._MAX_ATTEMPTS
        assert results.count(False) == 20 - auth._MAX_ATTEMPTS

    def test_release_removes_only_latest_attempt(self, app, monkeypatch):
        with app.test_request_context():
            monkeypatch.setattr(auth.time, "time", lambda: 100)
            assert auth.reserve_attempt()
            monkeypatch.setattr(auth.time, "time", lambda: 101)
            assert auth.reserve_attempt()
            auth.release_attempt()
            assert auth._attempts[auth._client_key()] == [100]
            auth.release_attempt()
            auth.release_attempt()
            assert auth._client_key() not in auth._attempts


@pytest.mark.parametrize("username,password", [("wrong-user", "first-password"), ("", ""), ("admin", "")])
def test_password_hash_always_checked(app, monkeypatch, username, password):
    calls = []
    original = auth.check_password_hash

    def spy(pwhash, supplied):
        calls.append(supplied)
        return original(pwhash, supplied)

    monkeypatch.setattr(auth, "check_password_hash", spy)
    with app.test_request_context():
        assert not auth.check_first_factor(username, password)
    assert calls == [password]
