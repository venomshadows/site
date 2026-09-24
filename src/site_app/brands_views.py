"""Страницы бренда и общий контекст сайдбара и вкладок."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from site_app import brand_client, domains
from site_app.auth import login_required

brands_bp = Blueprint('brands', __name__, url_prefix='/brands')

# Один реестр для маршрута и шаблона; первая вкладка открывается по умолчанию.
TABS = {
    'yandex': {'title': 'Яндекс', 'logo': 'yandex', 'domains': True},
    'google': {'title': 'Google', 'logo': 'google', 'domains': True},
    'drops': {'title': 'Дропы', 'logo': None, 'domains': False},
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
        sort, order = domains.sorting(request.args.get('sort'), request.args.get('order'))
        extra = dict(domain_rows=domains.list_tree(brand_id, tab, sort, order),
                     statuses=domains.STATUSES, sorts=domains.SORTS, sort=sort, order=order)
    return render_brand_page(
        'brand_tab.html', brands=brands, brands_error=brands_error,
        brand=brand, brand_id=brand_id, active_tab=tab, **extra,
    )


def _domain_destination(brand_id, engine):
    if not TABS.get(engine, {}).get('domains'):
        abort(404)
    return url_for('brands.brand_tab', brand_id=brand_id, tab=engine,
                   **{key: request.args[key] for key in ('sort', 'order') if key in request.args})


@brands_bp.post('/<int:brand_id>/<engine>/domains')
@login_required
def domains_add(brand_id, engine):
    destination = _domain_destination(brand_id, engine)
    added, skipped = domains.add_domains(brand_id, engine, request.form.get('domains', ''))
    flash(f'Добавлено доменов: {len(added)}.', 'success')
    if skipped:
        flash('Пропущено: ' + '; '.join(f'{value} — {reason}' for value, reason in skipped), 'error')
    return redirect(destination)


@brands_bp.post('/<int:brand_id>/<engine>/domains/bulk')
@login_required
def domains_bulk(brand_id, engine):
    destination = _domain_destination(brand_id, engine)
    ids = request.form.getlist('ids')
    try:
        action = request.form.get('action')
        if action == 'delete':
            count = domains.delete_domains(brand_id, engine, ids)
            flash(f'Удалено доменов: {count}.', 'success')
        elif action == 'status':
            count = domains.change_status(brand_id, engine, ids, request.form.get('status'),
                                          request.form.get('parent_id'))
            flash(f'Изменено доменов: {count}.', 'success')
        else:
            raise domains.DomainError('Неизвестное действие.')
    except domains.DomainError as exc:
        flash(str(exc), 'error')
    return redirect(destination)
