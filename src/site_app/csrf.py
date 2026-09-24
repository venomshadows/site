"""Защита форм, использующих браузерную сессию."""
import hmac
import secrets

from flask import abort, request, session


_API_PATH_PREFIX = "/api/v1/"


def _is_exempt_api_request() -> bool:
    # Проверяем принадлежность к api, включая будущие вложенные blueprint api.*.
    # Дополнительно требуем префикс пути, чтобы случайный endpoint-тёзка api.*
    # вне API не получил исключение из CSRF-проверки.
    return "api" in request.blueprints and request.path.startswith(_API_PATH_PREFIX)


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _protect_request() -> None:
    # API использует ключ в заголовке и не зависит от браузерной сессии.
    if _is_exempt_api_request() or request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    expected = session.get("csrf_token")
    supplied = request.form.get("csrf_token")
    if not expected or not supplied or not hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
        abort(400, description="Токен CSRF устарел или отсутствует. Обновите страницу.")


def init_app(app) -> None:
    app.add_template_global(csrf_token)
    app.before_request(_protect_request)
