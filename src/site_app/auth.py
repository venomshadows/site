"""
Двухфакторный вход (логин+пароль, затем второй пароль) сессиями Flask.

Ничего не хранится в коде — логин, оба хеша паролей и секрет для подписи
сессии берутся из переменных окружения (см. .env.example). Хеши паролей
генерируются скриптом deploy/gen_password_hash.py, сам пароль в открытом
виде никуда не сохраняется и не должен попадать в git.
"""

from __future__ import annotations

import threading
import time
from functools import wraps

from site_app import db

from flask import jsonify, after_this_request, current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash

# Стадии сессии: ключа "stage" нет — не вошёл; 1 — прошёл логин/пароль;
# 2 — прошёл и второй пароль.
STAGE_FIRST = 1
STAGE_FULL = 2

_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 300  # 5 минут

# Простой троттлинг попыток в памяти процесса — без внешней БД, сбрасывается
# при перезапуске сервиса. Для личного/малопосещаемого сайта этого достаточно.
# Сервис работает одним процессом gunicorn с потоками (см.
# deploy/site.service), поэтому счётчик общий для всех запросов,
# но доступ к нему нужно защищать блокировкой — иначе параллельные попытки
# читают и перезаписывают список одновременно.
_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _client_key() -> str:
    """Реальный IP клиента. Берём именно request.remote_addr: ProxyFix (см.
    webapp.py) уже подставил туда адрес, который дописал nginx — то есть
    правый, доверенный элемент X-Forwarded-For. Читать заголовок напрямую
    нельзя: его левую часть присылает сам клиент, и, подставляя каждый раз
    новое значение, он бесконечно сбрасывал бы себе лимит попыток."""
    return request.remote_addr or "unknown"


def _live_attempts(key: str, now: float) -> list[float]:
    """Попытки за последнее окно. Протухшие выбрасываем, а ключ без попыток
    удаляем целиком — иначе словарь рос бы вечно по числу заходивших IP.
    Вызывать только под _attempts_lock."""
    attempts = [t for t in _attempts.get(key, ()) if now - t < _WINDOW_SECONDS]
    if attempts:
        _attempts[key] = attempts
    else:
        _attempts.pop(key, None)
    return attempts


def is_rate_limited() -> bool:
    key = _client_key()
    with _attempts_lock:
        return len(_live_attempts(key, time.time())) >= _MAX_ATTEMPTS


def record_failed_attempt() -> None:
    key = _client_key()
    now = time.time()
    with _attempts_lock:
        attempts = _live_attempts(key, now)
        attempts.append(now)
        _attempts[key] = attempts


def clear_attempts() -> None:
    key = _client_key()
    with _attempts_lock:
        _attempts.pop(key, None)


def check_first_factor(username: str, password: str) -> bool:
    cfg = current_app.config
    if not username or not password:
        return False
    return username == cfg["AUTH_USERNAME"] and check_password_hash(cfg["AUTH_PASSWORD_HASH"], password)


def check_second_factor(password: str) -> bool:
    cfg = current_app.config
    if not password:
        return False
    return check_password_hash(cfg["AUTH_SECOND_PASSWORD_HASH"], password)


def no_store(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        @after_this_request
        def prevent_caching(response):
            response.headers["Cache-Control"] = "no-store"
            return response

        return view(*args, **kwargs)

    return wrapped


def login_required(view):
    @no_store
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("stage") != STAGE_FULL:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def _extract_api_key() -> str:
    """Ключ ищем в заголовке Authorization: Bearer <ключ>, а если его нет —
    в заголовке X-API-Key (удобнее для некоторых HTTP-клиентов/прокси,
    которые сложно настроить на Bearer-схему). Та же схема, что в
    rkn-checker."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer ") :].strip()
    return request.headers.get("X-API-Key", "").strip()


def api_key_required(view):
    """Для маршрутов внешнего API (/api/v1/*) — отдельная от
    сессионной аутентификации (login_required) схема: сравнение ключа из
    заголовка с активным ключом из settings.api_key (см. db.py). Ключ
    передаётся заголовком, а не куки сессии, так что запрос не зависит от
    браузерного состояния — им может пользоваться сторонний сервис
    напрямую, без входа через /login."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        key = _extract_api_key()
        if not db.verify_api_key(key):
            return jsonify(error="Неверный или отсутствующий API-ключ"), 401
        return view(*args, **kwargs)

    return wrapped
