"""Общий список дропов с фильтром бренда."""
from flask import Blueprint, redirect, render_template, request, url_for

from site_app import brand_client, domains, drops
from site_app.auth import login_required
from site_app.domain_forms import add_from_form, bulk_from_form

drops_bp = Blueprint('drops', __name__)


def _parse_brand_filter(raw):
    if raw == 'none':
        return 'none'
    if raw and raw.isascii() and raw.isdigit() and len(raw) <= 19:
        value = int(raw)
        if value <= 9223372036854775807:
            return value
    return 'all'


def _query():
    sort, order = domains.sorting(request.args.get('sort'), request.args.get('order'))
    return dict(brand=_parse_brand_filter(request.args.get('brand')), sort=sort, order=order)


@drops_bp.get('/drops')
@login_required
def drops_index():
    brands, brands_error = brand_client.list_brands()
    query = _query()
    domain_rows = drops.list_tree(drops.all_scope(query['brand']), query['sort'], query['order'])
    return render_template(
        'drops.html', brands=brands, brands_error=brands_error, brand_filter=query['brand'],
        domain_rows=domain_rows, statuses=domains.STATUSES, sorts=domains.SORTS,
        sort=query['sort'], order=query['order'], brand_names={b['id']: b['name'] for b in brands},
        history=drops.history_for([r['id'] for r in domain_rows]), assignable_brands=brands,
        show_brand_column=True, show_history=True, brand_actions=True, brand_select_on_add=True,
        add_url=url_for('drops.domains_add', **query), bulk_url=url_for('drops.domains_bulk', **query))


@drops_bp.post('/drops/domains')
@login_required
def domains_add():
    brand_id = _parse_brand_filter(request.form.get('brand_id'))
    add_from_form(drops.add_scope(brand_id if isinstance(brand_id, int) else None))
    query = _query()
    query['brand'] = brand_id if isinstance(brand_id, int) else 'none'
    return redirect(url_for('drops.drops_index', **query))


@drops_bp.post('/drops/domains/bulk')
@login_required
def domains_bulk():
    query = _query()
    bulk_from_form(drops.all_scope(query['brand']))
    return redirect(url_for('drops.drops_index', **query))
