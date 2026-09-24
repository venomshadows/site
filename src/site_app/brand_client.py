"""Список брендов из Brand; общий транспорт — в api_client.py."""

from __future__ import annotations

from site_app import api_client

# Прод Brand — фиксированный адрес; переменной окружения можно подменить
# для локальной разработки/тестов на другом инстансе, без правки кода.
_DEFAULT_API_BASE = "https://brand.venomshadows.ru"
_SERVICE_NAME = "Brand"


def _api_base() -> str:
    return api_client.resolve_base_url("BRAND_API_URL", _DEFAULT_API_BASE)


def list_brands() -> tuple[list[dict], str | None]:
    """(бренды, ошибка) — бренды в том же порядке, что отдаёт Brand (по
    имени), с суммарной частотой запросов (query_frequency_total, может
    быть None). Пустой список с ошибкой ≠ пустой список без брендов —
    вызывающий код должен показывать эти два случая по-разному.

    Записи без "id" или "name" отбрасываются здесь же, один раз для всех
    вызывающих: id нужен для ссылки на бренд (url_for уронил бы страницу,
    попытавшись собрать URL без него), name — чтобы вообще было что
    показать в сайдбаре."""
    result = api_client.get(
        service_name=_SERVICE_NAME, api_key_field="brand_api_key", base_url=_api_base(), path="/api/v1/brands"
    )
    if not result.ok:
        return [], result.error
    brands, error = api_client.extract_list(result.data, "brands", service_name=_SERVICE_NAME)
    if error:
        return [], error
    return [b for b in brands if type(b.get("id")) is int and b.get("name")], None

