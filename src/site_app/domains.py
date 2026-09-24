"""Локальные списки доменов: нормализация, дерево и атомарные операции."""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import idna

from site_app.db import _connect

STATUSES = {
    'new': {'title': 'Новый', 'css': 'domain-status--new'},
    'in_use': {'title': 'Используется', 'css': 'domain-status--in-use'},
    'used': {'title': 'Использован', 'css': 'domain-status--used'},
    'glued': {'title': 'В клее', 'css': 'domain-status--glued'},
}
SORTS = {'created_at': 'Дата добавления', 'status': 'Статус', 'registered_at': 'Дата регистрации'}


class DomainError(ValueError):
    """Ошибка операции, которую можно показать пользователю."""


def normalize_domain(raw):
    host = raw.strip()
    if not host:
        raise DomainError('пустая строка')
    host = re.sub(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', '', host)
    host = re.split(r'[/?#]', host, maxsplit=1)[0]
    if ':' in host:
        host, port = host.rsplit(':', 1)
        if not port.isascii() or not port.isdigit():
            raise DomainError('некорректный порт')
    host = host.lower().removesuffix('.')
    if not host or re.search(r'\s|@', host):
        raise DomainError('пустой хост или недопустимые символы')
    try:
        canonical = idna.encode(host).decode('ascii')
    except idna.IDNAError as exc:
        raise DomainError('некорректная метка домена') from exc
    if '.' not in canonical or len(canonical) > 253:
        raise DomainError('нужен домен с точкой длиной не более 253 символов')
    return canonical


def display_domain(domain):
    # Строка, попавшая в БД в обход normalize_domain (ручная правка, импорт),
    # может не декодироваться: показываем её как есть, а не роняем страницу
    # или весь /api/v1/sites 500-й.
    try:
        return idna.decode(domain)
    except (idna.IDNAError, UnicodeError, ValueError):
        return domain


@dataclass(frozen=True)
class ListScope:
    table: str
    where_sql: str
    where_params: tuple = ()
    insert_columns: dict = field(default_factory=dict)
    unique_where_sql: str | None = None
    unique_where_params: tuple | None = None
    glue_columns: tuple = ()

    @property
    def unique_sql(self):
        return self.where_sql if self.unique_where_sql is None else self.unique_where_sql

    @property
    def unique_params(self):
        return self.where_params if self.unique_where_params is None else self.unique_where_params


def brand_engine_scope(brand_id, engine):
    return ListScope(table='domains', where_sql='brand_id=? AND engine=?',
                     where_params=(brand_id, engine),
                     insert_columns={'brand_id': brand_id, 'engine': engine})


def add_domains(scope, text, on_insert=None):
    """Разделяем ввод по пробелам, запятым и переносам строк вперемешку.

    Возвращаем добавленные канонические домены и пары (ввод, причина пропуска).
    """
    added, skipped, seen = [], [], set()
    with _connect() as conn:
        # Блокировка до чтения защищает предварительную проверку от гонки вставок.
        conn.execute('BEGIN IMMEDIATE')
        existing = {r['domain'] for r in conn.execute(
            f'SELECT domain FROM {scope.table} WHERE {scope.unique_sql}', scope.unique_params)}
        for token in filter(None, re.split(r'[\s,]+', text)):
            try:
                domain = normalize_domain(token)
            except DomainError as exc:
                skipped.append((token, f'некорректный домен: {exc}'))
                continue
            if domain in seen:
                skipped.append((token, 'дубликат в списке'))
            elif domain in existing:
                skipped.append((token, 'уже есть в списке'))
            else:
                added.append(domain)
            seen.add(domain)
        now = datetime.now(timezone.utc).isoformat()
        columns = [*scope.insert_columns.keys(), 'domain', 'created_at']
        conn.executemany(
            f'INSERT INTO {scope.table} ({", ".join(columns)}) VALUES ({_marks(columns)})',
            [(*scope.insert_columns.values(), domain, now) for domain in added])
        if added and on_insert is not None:
            on_insert(conn, added)
    return added, skipped


def sorting(sort=None, order=None):
    return (sort if sort in SORTS else 'created_at', order if order in {'asc', 'desc'} else 'desc')


def list_tree(scope, sort=None, order=None):
    sort, order = sorting(sort, order)
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f'SELECT * FROM {scope.table} WHERE {scope.where_sql}', scope.where_params)]
    children = {}
    ranks = {status: rank for rank, status in enumerate(STATUSES)}
    for row in rows:
        row['display_domain'] = display_domain(row['domain'])
        children.setdefault(row['parent_id'], []).append(row)
    for siblings in children.values():
        if sort == 'registered_at':
            siblings.sort(key=lambda r: (r[sort] is None, r[sort]), reverse=order == 'desc')
            # Второй стабильный проход всегда оставляет NULL в конце.
            siblings.sort(key=lambda r: r[sort] is None)
        else:
            siblings.sort(key=lambda r: (ranks[r['status']] if sort == 'status' else r[sort], r['id']),
                          reverse=order == 'desc')
    result = []
    stack = [(r, 0) for r in reversed(children.get(None, []))]
    while stack:
        row, depth = stack.pop()
        result.append({**row, 'depth': depth})
        stack.extend((child, depth + 1) for child in reversed(children.get(row['id'], [])))
    return result


def would_cycle(parents, domain_id, parent_id):
    visited = set()
    while parent_id is not None:
        if parent_id == domain_id or parent_id in visited:
            return True
        visited.add(parent_id)
        parent_id = parents.get(parent_id)
    return False


def _selected(conn, scope, ids):
    # Некорректные и чужие id не влияют на остальные выбранные строки.
    valid = {int(value) for value in ids if str(value).isascii() and str(value).isdigit()
             and 0 < int(value) <= 9223372036854775807}
    if not valid:
        return []
    return [r['id'] for r in conn.execute(
        f'SELECT id FROM {scope.table} WHERE {scope.where_sql} AND id IN ({_marks(valid)})',
        (*scope.where_params, *valid))]


def _marks(ids):
    return ','.join('?' for _ in ids)


def change_status(scope, ids, status, parent_id=None):
    if status not in STATUSES:
        raise DomainError('Неизвестный статус.')
    with _connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        selected = _selected(conn, scope, ids)
        parent = None
        if status == 'glued':
            columns = ', '.join(('id', 'parent_id', *scope.glue_columns))
            all_rows = {r['id']: dict(r) for r in conn.execute(
                f'SELECT {columns} FROM {scope.table} WHERE {scope.where_sql}', scope.where_params)}
            parents = {item: row['parent_id'] for item, row in all_rows.items()}
            try:
                parent = int(parent_id)
            except (ValueError, TypeError):
                raise DomainError('Выберите родительский домен.') from None
            if parent not in parents:
                raise DomainError('Родитель должен принадлежать этому списку.')
            if parent in selected or any(would_cycle(parents, item, parent) for item in selected):
                raise DomainError('Нельзя приклеить домен к себе, потомку или домену из выбранного набора.')
            if scope.glue_columns and any(
                tuple(all_rows[parent][c] for c in scope.glue_columns)
                != tuple(all_rows[item][c] for c in scope.glue_columns) for item in selected
            ):
                raise DomainError('Нельзя приклеить домен к родителю из другого бренда.')
        if selected:
            conn.execute(f'UPDATE {scope.table} SET status=?, parent_id=? WHERE id IN ({_marks(selected)}) '
                         f'AND {scope.where_sql}',
                         (status, parent, *selected, *scope.where_params))
    return len(selected)


def delete_domains(scope, ids, before_delete=None):
    with _connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        selected = _selected(conn, scope, ids)
        if selected:
            marks = _marks(selected)
            conn.execute(f"""UPDATE {scope.table} SET parent_id=NULL, status='used'
                WHERE parent_id IN ({marks}) AND id NOT IN ({marks}) AND {scope.where_sql}""",
                         (*selected, *selected, *scope.where_params))
            if before_delete is not None:
                before_delete(conn, selected)
            conn.execute(f'DELETE FROM {scope.table} WHERE id IN ({marks}) AND {scope.where_sql}',
                         (*selected, *scope.where_params))
    return len(selected)
