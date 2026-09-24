"""Страницы бренда и общий контекст сайдбара и вкладок."""
from flask import Blueprint, abort, redirect, render_template, request, url_for
from site_app import brand_client, domains, drops
from site_app.auth import login_required
from site_app.domain_forms import add_from_form, bulk_from_form

brands_bp = Blueprint('brands', __name__, url_prefix='/brands')

# Один реестр для маршрута и шаблона; первая вкладка открывается по умолчанию.
TABS = {
    'yandex': {'title': 'Яндекс', 'logo': 'yandex', 'domains': True},
    'google': {'title': 'Google', 'logo': 'google', 'domains': True},
    'drops': {'title': 'Дропы', 'logo': None, 'domains': True},
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
    extra = {}
    if TABS[tab]['domains']:
        scope = _scope(brand_id, tab)
        sort, order = domains.sorting(request.args.get('sort'), request.args.get('order'))
        extra = dict(domain_rows=domains.list_tree(scope, sort, order),
                     statuses=domains.STATUSES, sorts=domains.SORTS, sort=sort, order=order,
                     show_brand_column=False, show_history=tab == 'drops',
                     brand_actions=tab == 'drops', brand_select_on_add=False,
                     add_url=url_for('brands.domains_add', brand_id=brand_id, engine=tab, sort=sort, order=order),
                     bulk_url=url_for('brands.domains_bulk', brand_id=brand_id, engine=tab, sort=sort, order=order))
        if tab == 'drops':
            extra.update(history=drops.history_for([r['id'] for r in extra['domain_rows']]),
                         assignable_brands=brands)
    return render_brand_page(
        'brand_tab.html', brands=brands, brands_error=brands_error,
        brand=brand, brand_id=brand_id, active_tab=tab, **extra,
    )


def _scope(brand_id, engine):
    return drops.brand_scope(brand_id) if engine == 'drops' else domains.brand_engine_scope(brand_id, engine)


def _domain_destination(brand_id, engine):
    if not TABS.get(engine, {}).get('domains'):
        abort(404)
    return url_for('brands.brand_tab', brand_id=brand_id, tab=engine,
                   **{key: request.args[key] for key in ('sort', 'order') if key in request.args})


@brands_bp.post('/<int:brand_id>/<engine>/domains')
@login_required
def domains_add(brand_id, engine):
    destination = _domain_destination(brand_id, engine)
    add_from_form(_scope(brand_id, engine))
    return redirect(destination)


@brands_bp.post('/<int:brand_id>/<engine>/domains/bulk')
@login_required
def domains_bulk(brand_id, engine):
    destination = _domain_destination(brand_id, engine)
    bulk_from_form(_scope(brand_id, engine))
    return redirect(destination)
