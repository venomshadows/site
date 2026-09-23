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
                auth.record_failed_attempt()
            assert not (auth.is_rate_limited())

    def test_rate_limited_at_threshold(self, app):
        with app.test_request_context():
            for _ in range(auth._MAX_ATTEMPTS):
                auth.record_failed_attempt()
            assert (auth.is_rate_limited())

    def test_clear_attempts_resets_limit(self, app):
        with app.test_request_context():
            for _ in range(auth._MAX_ATTEMPTS):
                auth.record_failed_attempt()
            auth.clear_attempts()
            assert not (auth.is_rate_limited())

    def test_attempts_expire_after_window(self, app):
        with app.test_request_context():
            for _ in range(auth._MAX_ATTEMPTS):
                auth.record_failed_attempt()
            # Сдвигаем записанные попытки за пределы окна вместо ожидания
            # реальных пяти минут.
            key = auth._client_key()
            expired = [t - auth._WINDOW_SECONDS - 1 for t in auth._attempts[key]]
            auth._attempts[key] = expired
            assert not (auth.is_rate_limited())

    def test_expired_attempts_do_not_leak_keys(self, app):
        """Ключи протухших попыток не должны накапливаться в памяти."""
        with app.test_request_context():
            auth.record_failed_attempt()
            key = auth._client_key()
            auth._attempts[key] = [auth._attempts[key][0] - auth._WINDOW_SECONDS - 1]
            auth.is_rate_limited()
            assert key not in auth._attempts

    def test_limit_is_per_client_address(self, app):
        with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.1"}):
            for _ in range(auth._MAX_ATTEMPTS):
                auth.record_failed_attempt()
            assert (auth.is_rate_limited())
        with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.2"}):
            assert not (auth.is_rate_limited())


