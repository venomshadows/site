"""Реестр дропов и дерево использования внутри выбранного бренда."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from site_app import brand_client, domains, drops
from site_app.auth import login_required
from site_app.domain_forms import (add_from_form, bulk_from_form, flash_added, flash_brand_report,
                                   form_brand_ids, resolve_targets, brand_names)

drops_bp = Blueprint('drops', __name__)


def _parse_brand_filter(raw):
    if raw == 'none':
        return 'none'
    if raw and raw.isascii() and raw.isdigit() and len(raw) <= 19:
        value = int(raw)
        if 0 < value <= 9223372036854775807:
            return value
    return 'all'


def _query():
    brand = _parse_brand_filter(request.args.get('brand'))
    sort, order = domains.sorting(request.args.get('sort'), request.args.get('order'))
    if not isinstance(brand, int) and sort not in drops.REGISTRY_SORTS:
        sort = 'created_at'
    return dict(brand=brand, sort=sort, order=order)


@drops_bp.get('/drops')
@login_required
def drops_index():
    brands, brands_error = brand_client.list_brands()
    names = {b['id']: b['name'] for b in brands}
    query = _query()
    brand = query['brand']
    context = isinstance(brand, int)
    rows = (drops.list_tree(drops.brand_context_scope(brand), query['sort'], query['order']) if context
            else drops.registry(brand, query['sort'], query['order']))
    ids = [r['drop_id'] if context else r['id'] for r in rows]
    registry_context = {}
    if not context:
        active = drops.brands_for_drops(ids)
        # Снять можно и бренд, исчезнувший из внешнего справочника.
        removable = {r['brand_id']: names.get(r['brand_id'], f"Бренд #{r['brand_id']}")
                     for entries in active.values() for r in entries}
        registry_context = dict(active_brands=active, removable_brands=removable)
    return render_template(
        'drops.html', brands=brands, brands_error=brands_error, brand_filter=brand,
        filter_title=names.get(brand, f'Бренд #{brand}') if context else ('без бренда' if brand == 'none' else 'все'),
        domain_rows=rows, statuses=domains.STATUSES, sorts=domains.SORTS if context else drops.REGISTRY_SORTS,
        sort=query['sort'], order=query['order'], brand_names=names,
        history=drops.history_for(ids, brand if context else None), **registry_context,
        drop_context=context, show_history=True,
        add_url=url_for('drops.domains_add', **query), bulk_url=url_for('drops.domains_bulk', **query))


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
    return redirect(url_for('drops.drops_index', **query))


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
    return redirect(url_for('drops.drops_index', **query))
