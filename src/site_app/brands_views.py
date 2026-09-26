"""Страницы бренда и общий контекст сайдбара и вкладок."""
from flask import Blueprint, abort, redirect, render_template, url_for
from site_app import brand_client, domains, drops, list_views
from site_app.auth import login_required
from site_app.domain_forms import add_from_form, bulk_from_form

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
    canonical = list_views.canonical_redirect()
    if canonical is not None:
        return canonical
    brands, brands_error = brand_client.list_brands()
    brand = next((b for b in brands if b.get('id') == brand_id), None)
    if brand is None and not brands_error:
        abort(404)
    scope = _scope(brand_id, tab)
    query = list_views.query()
    rows = domains.list_tree(scope, query['sort'], query['dir'])
    extra = dict(**domains.list_filters(rows, query.get('q', ''), query.get('status', '')),
                 statuses=domains.STATUSES, sort=query['sort'], direction=query['dir'],
                 show_history=tab == 'drops',
                 drop_context=tab == 'drops',
                 add_url=url_for('brands.domains_add', brand_id=brand_id, engine=tab, **list_views.url_params(query)),
                 bulk_url=url_for('brands.domains_bulk', brand_id=brand_id, engine=tab, **list_views.url_params(query)))
    if tab == 'drops':
        extra.update(history=drops.history_for([r['drop_id'] for r in extra['domain_rows']], brand_id),
                     brand_names={b['id']: b['name'] for b in brands})
    return render_brand_page(
        'brand_tab.html', brands=brands, brands_error=brands_error,
        brand=brand, brand_id=brand_id, active_tab=tab, **extra,
    )


def _scope(brand_id, engine):
    return drops.brand_context_scope(brand_id) if engine == 'drops' else domains.brand_engine_scope(brand_id, engine)


def _domain_destination(brand_id, engine):
    if engine not in TABS:
        abort(404)
    return url_for('brands.brand_tab', brand_id=brand_id, tab=engine,
                   **list_views.url_params(list_views.query()))


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
