"""Страницы бренда и общий контекст сайдбара и вкладок."""
from flask import Blueprint, abort, render_template
from site_app import brand_client
from site_app.auth import login_required

brands_bp = Blueprint('brands', __name__, url_prefix='/brands')

# Один реестр для маршрута и шаблона; первая вкладка открывается по умолчанию.
TABS = {
    'yandex': {'title': 'Яндекс', 'logo': 'yandex'},
    'google': {'title': 'Google', 'logo': 'google'},
    'drops': {'title': 'Дропы', 'logo': None},
}
DEFAULT_TAB = next(iter(TABS))


def render_brand_page(template_name, *, brands, brands_error, brand, brand_id, active_tab, **extra):
    # Выбор бренда готовит вызывающий маршрут: у главной и вкладки разная
    # логика отсутствующего id. Общий контекст задаётся только здесь.
    return render_template(
        template_name, brands=brands, brands_error=brands_error, brand=brand,
        brand_id=brand_id, active_tab=active_tab, tabs=TABS, default_tab=DEFAULT_TAB, **extra,
    )


@brands_bp.get('/<int:brand_id>/<tab>')
@login_required
def brand_tab(brand_id: int, tab: str):
    if tab not in TABS:
        abort(404)
    brands, brands_error = brand_client.list_brands()
    brand = next((b for b in brands if b.get('id') == brand_id), None)
    if brand is None and not brands_error:
        abort(404)
    return render_brand_page(
        'brand_tab.html', brands=brands, brands_error=brands_error,
        brand=brand, brand_id=brand_id, active_tab=tab,
    )
