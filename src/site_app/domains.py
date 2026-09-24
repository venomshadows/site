"""Локальные списки доменов: нормализация, дерево и атомарные операции."""
import re
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
    return idna.decode(domain)


def add_domains(brand_id, engine, text):
    """Разделяем ввод по пробелам, запятым и переносам строк вперемешку.

    Возвращаем добавленные канонические домены и пары (ввод, причина пропуска).
    """
    added, skipped, seen = [], [], set()
    with _connect() as conn:
        # Блокировка до чтения защищает предварительную проверку от гонки вставок.
        conn.execute('BEGIN IMMEDIATE')
        existing = {r['domain'] for r in conn.execute(
            'SELECT domain FROM domains WHERE brand_id=? AND engine=?', (brand_id, engine))}
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
        conn.executemany(
            'INSERT INTO domains (brand_id, engine, domain, created_at) VALUES (?, ?, ?, ?)',
            [(brand_id, engine, domain, now) for domain in added])
    return added, skipped


def sorting(sort=None, order=None):
    return (sort if sort in SORTS else 'created_at', order if order in {'asc', 'desc'} else 'desc')


def list_tree(brand_id, engine, sort=None, order=None):
    sort, order = sorting(sort, order)
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM domains WHERE brand_id=? AND engine=?', (brand_id, engine))]
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


def _selected(conn, brand_id, engine, ids):
    # Некорректные и чужие id не влияют на остальные выбранные строки.
    valid = {int(value) for value in ids if str(value).isascii() and str(value).isdigit()
             and 0 < int(value) <= 9223372036854775807}
    if not valid:
        return []
    return [r['id'] for r in conn.execute(
        f'SELECT id FROM domains WHERE brand_id=? AND engine=? AND id IN ({_marks(valid)})',
        (brand_id, engine, *valid))]


def _marks(ids):
    return ','.join('?' for _ in ids)


def change_status(brand_id, engine, ids, status, parent_id=None):
    if status not in STATUSES:
        raise DomainError('Неизвестный статус.')
    with _connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        selected = _selected(conn, brand_id, engine, ids)
        parent = None
        if status == 'glued':
            parents = {r['id']: r['parent_id'] for r in conn.execute(
                'SELECT id, parent_id FROM domains WHERE brand_id=? AND engine=?', (brand_id, engine))}
            try:
                parent = int(parent_id)
            except (ValueError, TypeError):
                raise DomainError('Выберите родительский домен.') from None
            if parent not in parents:
                raise DomainError('Родитель должен принадлежать этому списку.')
            if parent in selected or any(would_cycle(parents, item, parent) for item in selected):
                raise DomainError('Нельзя приклеить домен к себе, потомку или домену из выбранного набора.')
        if selected:
            conn.execute(f'UPDATE domains SET status=?, parent_id=? WHERE id IN ({_marks(selected)}) '
                         'AND brand_id=? AND engine=?',
                         (status, parent, *selected, brand_id, engine))
    return len(selected)


def delete_domains(brand_id, engine, ids):
    with _connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        selected = _selected(conn, brand_id, engine, ids)
        if selected:
            marks = _marks(selected)
            conn.execute(f"""UPDATE domains SET parent_id=NULL, status='used'
                WHERE parent_id IN ({marks}) AND id NOT IN ({marks}) AND brand_id=? AND engine=?""",
                         (*selected, *selected, brand_id, engine))
            conn.execute(f'DELETE FROM domains WHERE id IN ({marks}) AND brand_id=? AND engine=?',
                         (*selected, brand_id, engine))
    return len(selected)
