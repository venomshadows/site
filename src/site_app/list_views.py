"""Общий URL-контракт серверных списков."""
from urllib.parse import urlencode

from flask import redirect, request

from site_app import domains, drops


def _brand_filter(raw):
    if raw == 'none':
        return 'none'
    if raw and raw.isascii() and raw.isdigit() and len(raw) <= 19:
        value = int(raw)
        if 0 < value <= 9223372036854775807:
            return value
    return 'all'


def query(*, include_brand=False):
    sort = next((value for value in request.args.getlist('sort') if value), None)
    direction = next((value for value in request.args.getlist('dir') if value), None)
    sort, direction = domains.sorting(sort, direction or request.args.get('order'))
    params = dict(sort=sort, dir=direction, q=request.args.get('q', '').strip(),
                  status=request.args.get('status', '') if request.args.get('status') in domains.STATUSES else '')
    if include_brand:
        params = dict(brand=_brand_filter(request.args.get('brand')), **params)
        if not isinstance(params['brand'], int) and sort not in drops.REGISTRY_SORTS:
            params['sort'] = 'created_at'
    return {key: value for key, value in params.items() if value}


def url_params(params):
    return {key: value for key, value in params.items() if key != 'brand' or value != 'all'}


def canonical_redirect(*, include_brand=False):
    params = query(include_brand=include_brand)
    list_keys = {'sort', 'dir', 'order', 'q', 'status', 'brand'}
    # Отсутствующие параметры не требуют редиректа: URL без фильтров уже допустим.
    if not any(key not in params or values != [str(params[key])]
               for key, values in request.args.lists() if key in list_keys):
        return None
    args = [(key, value) for key, value in request.args.items(multi=True)
            if key not in list_keys]
    args.extend(url_params(params).items())
    return redirect(request.path + '?' + urlencode(args))
