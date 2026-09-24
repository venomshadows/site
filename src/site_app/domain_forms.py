"""Общие действия форм списков доменов и дропов."""
from flask import flash, request

from site_app import brand_client, domains, drops


def flash_added(added, skipped):
    flash(f'Добавлено доменов: {len(added)}.', 'success')
    if skipped:
        flash('Пропущено: ' + '; '.join(f'{value} — {reason}' for value, reason in skipped), 'error')


def brand_names():
    brands, error = brand_client.list_brands()
    return {b['id']: b['name'] for b in brands}, error


def form_brand_ids(field):
    result = []
    for raw in request.form.getlist(field):
        if not raw:
            continue
        if not raw.isascii() or not raw.isdigit() or len(raw) > 19 or not 0 < int(raw) <= 9223372036854775807:
            raise domains.DomainError('Некорректный бренд.')
        if int(raw) not in result:
            result.append(int(raw))
    return result


def resolve_targets(ids):
    if not ids:
        return []
    names, error = brand_names()
    if error:
        raise domains.DomainError(f'Присвоение брендов недоступно: {error}')
    if any(item not in names for item in ids):
        raise domains.DomainError('Бренд не найден.')
    return [(item, names[item]) for item in ids]


def flash_brand_report(report, names=None):
    for brand_id, result in report.items():
        name = (names or {}).get(brand_id, result['brand_name'])
        if 'assigned' in result:
            message = f"присвоен {result['assigned']} доменам, уже были в бренде: {result['already']}"
        else:
            message = f"снят у {result['removed']} доменов, не было в бренде: {result['already_absent']}"
        flash(f'{name}: {message}.', 'success')


def add_from_form(scope):
    try:
        if scope.table == 'drop_brands':
            brand_id = scope.where_params[0]
            name = resolve_targets([brand_id])[0][1]
            added, skipped, report = drops.add_to_brand(brand_id, name, request.form.get('domains', ''))
            flash_added(added, skipped)
            flash_brand_report(report)
        else:
            flash_added(*domains.add_domains(scope, request.form.get('domains', '')))
    except domains.DomainError as exc:
        flash(str(exc), 'error')


def bulk_from_form(scope):
    ids = request.form.getlist('ids')
    try:
        action = request.form.get('action')
        if action == 'delete':
            if scope.table == 'drop_brands':
                brand_id = scope.where_params[0]
                report = drops.remove_from_brand(brand_id, ids)
                names, _ = brand_names()
                flash_brand_report({brand_id: dict(brand_name=f'Бренд #{brand_id}', **report)}, names)
            else:
                count = domains.delete_domains(scope, ids)
                flash(f'Удалено доменов: {count}.', 'success')
        elif action == 'status':
            count = domains.change_status(scope, ids, request.form.get('status'), request.form.get('parent_id'))
            flash(f'Изменено доменов: {count}.', 'success')
        else:
            raise domains.DomainError('Неизвестное действие.')
    except domains.DomainError as exc:
        flash(str(exc), 'error')
