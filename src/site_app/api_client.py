"""
Общая часть HTTP-клиента для внешних сервисов семьи venomshadows, у
которых этот сайт по API забирает данные (Brand, Site, ...):
Authorization: Bearer <ключ из /settings>, best-effort запрос — сетевая
ошибка, таймаут, сам сервис временно недоступный или просто неожиданная
форма ответа не должны ронять страницу 500-й, только возвращать понятную
причину (см. ApiResult).

Конкретные endpoint'ы, поле ключа настроек и разбор своих полей ответа
остаются в каждом клиенте отдельно.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import requests

from site_app import db

_TIMEOUT = 8


def resolve_base_url(env_var: str, default: str) -> str:
    """"or", а не .get(..., default): переменная окружения, заданная, но
    пустой строкой (например, FOO_API_URL= в .env.example, скопированном
    как есть) — это НЕ "переопредели на пустоту", иначе запросы ушли бы по
    относительному пути вместо прода."""
    return (os.environ.get(env_var) or default).rstrip("/")


@dataclass
class ApiResult:
    ok: bool
    data: dict = field(default_factory=dict)
    error: str | None = None


def get(
    *, service_name: str, api_key_field: str, base_url: str, path: str, params: dict | None = None
) -> ApiResult:
    api_key = db.get_settings()[api_key_field]
    if not api_key:
        return ApiResult(ok=False, error=f"API-ключ {service_name} не задан — впиши его на /settings.")

    try:
        response = requests.get(
            f"{base_url}{path}",
            headers={"Authorization": f"Bearer {api_key}"},
            params=params,
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        # Только тип исключения, не его текст: у requests он может включать
        # значение заголовка Authorization (InvalidHeader — ключ с переводом
        # строки), а ошибка отсюда показывается на странице и пишется в
        # журнал.
        return ApiResult(ok=False, error=f"{service_name} недоступен: {type(exc).__name__}")

    if response.status_code == 401:
        return ApiResult(ok=False, error=f"{service_name} отклонил API-ключ — проверь его на /settings.")
    if not response.ok:
        return ApiResult(ok=False, error=f"{service_name} ответил ошибкой {response.status_code}.")

    try:
        payload = response.json()
    except ValueError:
        return ApiResult(ok=False, error=f"{service_name} вернул ответ, который не получилось разобрать как JSON.")
    # Дальше вызывающий код делает payload.get(...) — без этой проверки
    # ответ вроде null или "..." (валидный JSON, но не объект) уронил бы
    # его AttributeError'ом вместо понятной ошибки.
    if not isinstance(payload, dict):
        return ApiResult(ok=False, error=f"{service_name} вернул ответ неожиданной формы (не JSON-объект).")
    return ApiResult(ok=True, data=payload)


def extract_list(payload: dict, key: str, *, service_name: str) -> tuple[list[dict], str | None]:
    """payload[key], если он вообще есть в ответе, должен быть списком
    словарей (список записей). Отсутствие ключа — законное "данных нет"
    (пустой список, без ошибки); а вот ключ, который ЕСТЬ, но не список
    (например, null или строка) — уже нарушение контракта сервиса, и его
    стоит показать как ошибку, а не молча притвориться пустым списком."""
    if key not in payload:
        return [], None
    raw = payload[key]
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        return [], f'{service_name} вернул поле "{key}" в неожиданном формате.'
    return raw, None
