"""Области дропов и история переноса их поддеревьев между брендами."""
from datetime import datetime, timezone

from site_app import domains

list_tree = domains.list_tree


def add_domains(scope, text, brand_name=None):
    brand_id = scope.insert_columns.get('brand_id')

    def on_insert(conn, added):
        # Без снимка имени (например, бренд удалён) сохраняем добавление без истории.
        if brand_id is None or brand_name is None:
            return
        marks = ','.join('?' for _ in added)
        rows = conn.execute(
            f'SELECT id, created_at FROM drops WHERE brand_id=? AND domain IN ({marks})',
            (brand_id, *added))
        conn.executemany(
            'INSERT INTO drop_brand_history (drop_id, brand_id, brand_name, assigned_at, removed_at) '
            'VALUES (?, ?, ?, ?, NULL)',
            [(row['id'], brand_id, brand_name, row['created_at']) for row in rows])

    # Обработчик пишет историю до commit вставки: ошибка откатит обе операции,
    # тогда как отдельный вызов после add_domains нарушил бы атомарность.
    return domains.add_domains(scope, text, on_insert=on_insert)


def _scope(where_sql, where_params, insert_columns):
    # Клей допускается только внутри одного бренда, включая два NULL.
    return domains.ListScope(
        table='drops', where_sql=where_sql, where_params=where_params,
        insert_columns=insert_columns, unique_where_sql='1=1', unique_where_params=(),
        glue_columns=('brand_id',))


def brand_scope(brand_id):
    return _scope('brand_id=?', (brand_id,), {'brand_id': brand_id})


def all_scope(brand_filter):
    if brand_filter == 'none':
        return _scope('brand_id IS NULL', (), {})
    if isinstance(brand_filter, int):
        return _scope('brand_id=?', (brand_filter,), {})
    return _scope('1=1', (), {})


def add_scope(brand_id):
    return _scope('1=1', (), {'brand_id': brand_id})


def delete_domains(scope, ids):
    def before_delete(conn, deleted_ids):
        marks = ','.join('?' for _ in deleted_ids)
        # Проход в domains уже отклеил потомков внутри scope. При повреждённом клее
        # потомки могут быть и вне scope: расширяем поиск, чтобы не оставить битые ссылки.
        # Историю, напротив, удаляем только у реально удаляемых дропов.
        conn.execute(
            "UPDATE drops SET parent_id=NULL, status='used' "
            f'WHERE parent_id IN ({marks}) AND id NOT IN ({marks})',
            (*deleted_ids, *deleted_ids))
        conn.execute(f'DELETE FROM drop_brand_history WHERE drop_id IN ({marks})', deleted_ids)
    return domains.delete_domains(scope, ids, before_delete=before_delete)


def history_for(drop_ids):
    if not drop_ids:
        return {}
    with domains._connect() as conn:
        marks = ','.join('?' for _ in drop_ids)
        rows = conn.execute(
            'SELECT drop_id, brand_name, assigned_at, removed_at FROM drop_brand_history '
            f'WHERE drop_id IN ({marks}) ORDER BY assigned_at DESC, id DESC', tuple(drop_ids))
        result = {}
        for row in rows:
            result.setdefault(row['drop_id'], []).append(dict(row))
        return result


def assign_brand(scope, ids, target_brand_id, target_brand_name):
    return _transfer_brand(scope, ids, target_brand_id, target_brand_name)


def unassign_brand(scope, ids):
    return _transfer_brand(scope, ids, None, None)


def _transfer_brand(scope, ids, target_brand_id, target_brand_name):
    if target_brand_id is not None and not target_brand_name:
        raise domains.DomainError('Не удалось определить имя бренда для истории.')
    with domains._connect() as conn:
        # Снимок дерева, перенос и история меняются в одной транзакции.
        conn.execute('BEGIN IMMEDIATE')
        selected = domains._selected(conn, scope, ids)
        if not selected:
            return 0
        rows = {r['id']: r for r in conn.execute('SELECT id, parent_id, brand_id FROM drops')}
        children = {}
        for row in rows.values():
            children.setdefault(row['parent_id'], []).append(row['id'])
        closure = set(selected)
        pending = list(selected)
        while pending:
            for child in children.get(pending.pop(), ()):
                if child not in closure:
                    closure.add(child)
                    pending.append(child)
        marks = ','.join('?' for _ in closure)
        open_history_ids = {row['drop_id'] for row in conn.execute(
            'SELECT drop_id FROM drop_brand_history '
            f'WHERE removed_at IS NULL AND drop_id IN ({marks})', tuple(closure))}
        # Повторное присвоение восстанавливает историю, если при добавлении не было имени бренда.
        changed = sorted(item for item in closure
                         if rows[item]['brand_id'] != target_brand_id
                         or (target_brand_id is not None and item not in open_history_ids))
        if changed:
            now = datetime.now(timezone.utc).isoformat()
            marks = ','.join('?' for _ in changed)
            conn.execute('UPDATE drop_brand_history SET removed_at=? '
                         f'WHERE removed_at IS NULL AND drop_id IN ({marks})', (now, *changed))
            if target_brand_id is not None:
                conn.executemany(
                    'INSERT INTO drop_brand_history '
                    '(drop_id, brand_id, brand_name, assigned_at, removed_at) VALUES (?, ?, ?, ?, NULL)',
                    [(item, target_brand_id, target_brand_name, now) for item in changed])
            conn.execute(f'UPDATE drops SET brand_id=? WHERE id IN ({marks})', (target_brand_id, *changed))
        # Отклеиваем только при фактической смене бренда: восстановление истории в changed
        # не должно менять клей, даже при нарушенном инварианте.
        # Родитель вне closure сохраняет бренд: отделяем ветку только при разных брендах.
        detach_ids = [item for item in selected
                      if rows[item]['brand_id'] != target_brand_id
                      and rows[item]['parent_id'] is not None and rows[item]['parent_id'] not in closure
                      and rows[rows[item]['parent_id']]['brand_id'] != target_brand_id]
        if detach_ids:
            marks = ','.join('?' for _ in detach_ids)
            conn.execute(f"UPDATE drops SET parent_id=NULL, status='new' WHERE id IN ({marks})", detach_ids)
        return len(changed)
