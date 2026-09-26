"""Реестр дропов и независимые периоды их использования в брендах."""
from datetime import datetime, timezone

from site_app import domains

list_tree = domains.list_tree
change_status = domains.change_status
REGISTRY_SORTS = {key: domains.SORTS[key] for key in ('created_at', 'registered_at')}
REGISTRY_SCOPE = domains.ListScope(table='drops', where_sql='1=1')


def brand_context_scope(brand_id):
    return domains.ListScope(
        table='drop_brands', where_sql='brand_id=? AND removed_at IS NULL', where_params=(brand_id,),
        read_table='(SELECT db.*, d.domain, d.created_at, d.registered_at '
                   'FROM drop_brands db JOIN drops d ON d.id=db.drop_id)')


def remove_from_brand(brand_id, ids):
    with domains._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # Чужие и некорректные id игнорируем, закрытые периоды учитываем в отчёте.
        known = domains._selected(conn, domains.ListScope(
            table='drop_brands', where_sql='brand_id=?', where_params=(brand_id,)), ids)
        return _remove_brand_rows(conn, brand_id, known, len(known), datetime.now(timezone.utc).isoformat())


def _remove_brand_rows(conn, brand_id, ids, total, now):
    """Снятие, отклеивание детей и отчёт используют транзакцию вызывающего кода."""
    selected = domains._selected(conn, brand_context_scope(brand_id), ids)
    if selected:
        marks = domains._marks(selected)
        conn.execute("UPDATE drop_brands SET parent_id=NULL, status='used' "
                     f'WHERE parent_id IN ({marks}) AND id NOT IN ({marks}) '
                     'AND brand_id=? AND removed_at IS NULL', (*selected, *selected, brand_id))
        conn.execute(f'UPDATE drop_brands SET removed_at=? WHERE id IN ({marks}) '
                     'AND brand_id=? AND removed_at IS NULL', (now, *selected, brand_id))
    return dict(removed=len(selected), already_absent=total - len(selected))


def _open_brand_row(conn, drop_id, brand_id, brand_name, now):
    if conn.execute('SELECT 1 FROM drop_brands WHERE drop_id=? AND brand_id=? AND removed_at IS NULL',
                    (drop_id, brand_id)).fetchone():
        return False
    if not brand_name:
        raise domains.DomainError('Не удалось определить имя бренда для истории.')
    conn.execute('INSERT INTO drop_brands (drop_id, brand_id, brand_name, assigned_at) VALUES (?, ?, ?, ?)',
                 (drop_id, brand_id, brand_name, now))
    return True


def _ensure_drop(conn, domain, now):
    row = conn.execute('SELECT id FROM drops WHERE domain=?', (domain,)).fetchone()
    return row['id'] if row else conn.execute(
        'INSERT INTO drops (domain, created_at) VALUES (?, ?)', (domain, now)).lastrowid


def add_to_brand(brand_id, brand_name, text):
    with domains._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = {r['domain'] for r in conn.execute(
            'SELECT d.domain FROM drops d JOIN drop_brands db ON db.drop_id=d.id '
            'WHERE db.brand_id=? AND db.removed_at IS NULL', (brand_id,))}
        added, skipped = domains.parse_domain_tokens(text, existing)
        skipped = [(token, 'уже в этом бренде' if reason == 'уже есть в списке' else reason)
                   for token, reason in skipped]
        now = datetime.now(timezone.utc).isoformat()
        candidates, _ = domains.parse_domain_tokens(text, set())
        ids = [_ensure_drop(conn, domain, now) for domain in candidates]
        report = _assign(conn, ids, [(brand_id, brand_name)], now)
    return added, skipped, report


def add_to_registry(text, targets=()):
    """Добавляет домены и бренды атомарно, включая новые бренды уже известного домена."""
    with domains._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = {r['domain'] for r in conn.execute('SELECT domain FROM drops')}
        added, skipped = domains.parse_domain_tokens(text, set() if targets else existing)
        now = datetime.now(timezone.utc).isoformat()
        ids = [_ensure_drop(conn, domain, now) for domain in added]
        report = _assign(conn, ids, targets, now)
    # Присвоение бренда существующему домену отражает report, а счётчик
    # добавления в реестр должен учитывать только действительно новые домены.
    return [domain for domain in added if domain not in existing], skipped, report


def registry(brand_filter='all', sort=None, order=None):
    sort = sort if sort in REGISTRY_SORTS else 'created_at'
    order = order if order in {'asc', 'desc'} else 'desc'
    where = ('NOT EXISTS (SELECT 1 FROM drop_brands db WHERE db.drop_id=d.id AND db.removed_at IS NULL)'
             if brand_filter == 'none' else '1=1')
    with domains._connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f'SELECT d.* FROM drops d WHERE {where} ORDER BY {sort} IS NULL, {sort} {order}, id {order}')]
    return [{**row, 'display_domain': domains.display_domain(row['domain'])} for row in rows]


def registry_filters(rows, active_brands, q='', status=''):
    # Неназначенные домены не имеют статуса использования; видны в «Все».
    enriched = [{**row, 'statuses': [entry['status'] for entry in active_brands.get(row['id'], [])]}
                for row in rows]
    return domains.list_filters(enriched, q, status)


def _brand_rows(drop_ids, condition='', params=()):
    if not drop_ids:
        return {}
    with domains._connect() as conn:
        result = {}
        for row in conn.execute(
            f'SELECT * FROM drop_brands WHERE drop_id IN ({domains._marks(drop_ids)}) {condition} '
            'ORDER BY assigned_at DESC, id DESC', (*drop_ids, *params)):
            result.setdefault(row['drop_id'], []).append(dict(row))
        return result


def brands_for_drops(drop_ids):
    return _brand_rows(drop_ids, 'AND removed_at IS NULL')


def history_for(drop_ids, brand_id=None):
    """Реестр получает все периоды; контекст бренда — только периоды этого бренда."""
    return _brand_rows(drop_ids, 'AND brand_id=?' if brand_id is not None else '',
                       (brand_id,) if brand_id is not None else ())


def delete_domain(ids):
    with domains._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        selected = domains._selected(conn, REGISTRY_SCOPE, ids)
        if selected:
            marks = domains._marks(selected)
            # Удаляемый период мог быть родителем у оставшегося домена.
            # Статус меняем только у активных строк, сохраняя историю закрытых периодов.
            conn.execute("UPDATE drop_brands SET parent_id=NULL, status='used' WHERE parent_id IN "
                         f'(SELECT id FROM drop_brands WHERE drop_id IN ({marks})) '
                         f'AND drop_id NOT IN ({marks}) AND removed_at IS NULL', (*selected, *selected))
            conn.execute('UPDATE drop_brands SET parent_id=NULL WHERE parent_id IN '
                         f'(SELECT id FROM drop_brands WHERE drop_id IN ({marks})) '
                         f'AND drop_id NOT IN ({marks}) AND removed_at IS NOT NULL', (*selected, *selected))
            conn.execute(f'DELETE FROM drop_brands WHERE drop_id IN ({marks})', selected)
            conn.execute(f'DELETE FROM drops WHERE id IN ({marks})', selected)
    return len(selected)


def _assign(conn, ids, targets, now):
    report = {}
    for brand_id, brand_name in dict(targets).items():
        assigned = sum(_open_brand_row(conn, item, brand_id, brand_name, now) for item in ids)
        report[brand_id] = dict(brand_name=brand_name, assigned=assigned, already=len(ids) - assigned)
    return report


def assign_brands(ids, targets):
    with domains._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        selected = domains._selected(conn, REGISTRY_SCOPE, ids)
        return _assign(conn, selected, targets, datetime.now(timezone.utc).isoformat())


def unassign_brands(ids, brand_ids):
    with domains._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        selected = domains._selected(conn, REGISTRY_SCOPE, ids)
        if not selected:
            return {}
        report = {}
        now = datetime.now(timezone.utc).isoformat()
        for brand_id in dict.fromkeys(brand_ids):
            rows = list(conn.execute(
                f'SELECT id, brand_name FROM drop_brands WHERE drop_id IN ({domains._marks(selected)}) '
                'AND brand_id=? AND removed_at IS NULL', (*selected, brand_id)))
            result = _remove_brand_rows(conn, brand_id, [r['id'] for r in rows], len(selected), now)
            report[brand_id] = dict(brand_name=rows[0]['brand_name'] if rows else f'Бренд #{brand_id}',
                                   **result)
    return report
