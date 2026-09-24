"""Общие действия форм списков доменов и дропов."""
from flask import flash, request

from site_app import brand_client, domains, drops


def add_from_form(scope):
    text = request.form.get('domains', '')
    if scope.table == 'drops':
        brand_id = scope.insert_columns.get('brand_id')
        brand_name = None
        if brand_id is not None:
            brands, _ = brand_client.list_brands()
            brand_name = next((b['name'] for b in brands if b['id'] == brand_id), None)
        added, skipped = drops.add_domains(scope, text, brand_name=brand_name)
    else:
        added, skipped = domains.add_domains(scope, text)
    flash(f'Добавлено доменов: {len(added)}.', 'success')
    if skipped:
        flash('Пропущено: ' + '; '.join(f'{value} — {reason}' for value, reason in skipped), 'error')


def bulk_from_form(scope):
    is_drops = scope.table == 'drops'
    ids = request.form.getlist('ids')
    try:
        action = request.form.get('action')
        if action == 'delete':
            count = (drops if is_drops else domains).delete_domains(scope, ids)
            flash(f'Удалено доменов: {count}.', 'success')
        elif action == 'status':
            count = domains.change_status(scope, ids, request.form.get('status'), request.form.get('parent_id'))
            flash(f'Изменено доменов: {count}.', 'success')
        elif is_drops and action == 'assign_brand':
            brands, error = brand_client.list_brands()
            if error:
                raise domains.DomainError(f'Присвоение бренда недоступно: {error}')
            target = next((b for b in brands if str(b['id']) == request.form.get('target_brand_id')), None)
            if target is None:
                raise domains.DomainError('Бренд не найден')
            count = drops.assign_brand(scope, ids, target['id'], target['name'])
            flash(f'Перенесено дропов: {count}.', 'success')
        elif is_drops and action == 'unassign_brand':
            count = drops.unassign_brand(scope, ids)
            flash(f'Снято с бренда дропов: {count}.', 'success')
        else:
            raise domains.DomainError('Неизвестное действие.')
    except domains.DomainError as exc:
        flash(str(exc), 'error')
