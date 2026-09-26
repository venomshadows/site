"""Реестр дропов и дерево использования внутри выбранного бренда."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from site_app import brand_client, domains, drops, list_views
from site_app.auth import login_required
from site_app.domain_forms import (add_from_form, bulk_from_form, flash_added, flash_brand_report,
                                   form_brand_ids, resolve_targets, brand_names)

drops_bp = Blueprint('drops', __name__)


def _query():
    return list_views.query(include_brand=True)


def _context_page(brand, query):
    rows = drops.list_tree(drops.brand_context_scope(brand), query['sort'], query['dir'])
    return dict(**domains.list_filters(rows, query.get('q', ''), query.get('status', '')),
                history=drops.history_for([row['drop_id'] for row in rows], brand))


def _registry_page(brand, query, names):
    rows = drops.registry(brand, query['sort'], query['dir'])
    ids = [row['id'] for row in rows]
    active = drops.brands_for_drops(ids)
    # Снять можно и бренд, исчезнувший из внешнего справочника.
    removable = {entry['brand_id']: names.get(entry['brand_id'], f"Бренд #{entry['brand_id']}")
                 for entries in active.values() for entry in entries}
    return dict(**drops.registry_filters(rows, active, query.get('q', ''), query.get('status', '')),
                history=drops.history_for(ids), active_brands=active, removable_brands=removable)


@drops_bp.get('/drops')
@login_required
def drops_index():
    canonical = list_views.canonical_redirect(include_brand=True)
    if canonical is not None:
        return canonical
    brands, brands_error = brand_client.list_brands()
    names = {b['id']: b['name'] for b in brands}
    query = _query()
    brand = query['brand']
    context = isinstance(brand, int)
    page = _context_page(brand, query) if context else _registry_page(brand, query, names)
    return render_template(
        'drops.html', brands=brands, brands_error=brands_error, brand_filter=brand, brand_dropdown=True,
        filter_title=names.get(brand, f'Бренд #{brand}') if context else ('без бренда' if brand == 'none' else 'все'),
        **page, statuses=domains.STATUSES,
        sort=query['sort'], direction=query['dir'], brand_names=names,
        drop_context=context, show_history=True,
        add_url=url_for('drops.domains_add', **list_views.url_params(query)), bulk_url=url_for('drops.domains_bulk', **list_views.url_params(query)))


@drops_bp.post('/drops/domains')
@login_required
def domains_add():
    query = _query()
    if isinstance(query['brand'], int):
        add_from_form(drops.brand_context_scope(query['brand']))
    else:
        try:
            targets = resolve_targets(form_brand_ids('brand_ids'))
            added, skipped, report = drops.add_to_registry(request.form.get('domains', ''), targets)
            flash_added(added, skipped)
            flash_brand_report(report)
        except domains.DomainError as exc:
            flash(str(exc), 'error')
    # Фильтр берём только из query: выбор брендов в форме не меняет страницу.
    return redirect(url_for('drops.drops_index', **list_views.url_params(query)))


@drops_bp.post('/drops/domains/bulk')
@login_required
def domains_bulk():
    query = _query()
    if isinstance(query['brand'], int):
        bulk_from_form(drops.brand_context_scope(query['brand']))
    else:
        try:
            visible = {row['id'] for row in drops.registry(query['brand'])}
            ids = [item for item in request.form.getlist('ids') if item.isascii() and item.isdigit()
                   and len(item) <= 19 and int(item) in visible]
            action = request.form.get('action')
            if action == 'delete':
                flash(f'Удалено доменов: {drops.delete_domain(ids)}.', 'success')
            elif action in {'assign_brands', 'unassign_brands'}:
                brand_ids = form_brand_ids('target_brand_ids' if action == 'assign_brands' else 'remove_brand_ids')
                if not brand_ids:
                    raise domains.DomainError('Выберите хотя бы один бренд.')
                if action == 'assign_brands':
                    flash_brand_report(drops.assign_brands(ids, resolve_targets(brand_ids)))
                else:
                    names, _ = brand_names()
                    flash_brand_report(drops.unassign_brands(ids, brand_ids), names)
            else:
                raise domains.DomainError('Неизвестное действие.')
        except domains.DomainError as exc:
            flash(str(exc), 'error')
    return redirect(url_for('drops.drops_index', **list_views.url_params(query)))
