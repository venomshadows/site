"""Миграция, независимые бренды дропа, история и защита форм."""
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from site_app import brand_client, db, domains, drops

BRANDS = [{'id': 7, 'name': 'Первый'}, {'id': 8, 'name': 'Второй'}]
TARGETS = [(b['id'], b['name']) for b in BRANDS]


@pytest.fixture(autouse=True)
def brands():
    with patch.object(brand_client, 'list_brands', return_value=(BRANDS, None)) as fetch:
        yield fetch


def rows():
    return {r['domain']: r for r in drops.registry()}


def brand_rows(brand=7):
    return {r['domain']: r for r in drops.list_tree(drops.brand_context_scope(brand))}


def add(text='one.ru', targets=()):
    drops.add_to_registry(text, targets)
    return rows()[text.split()[0]]['id']


@pytest.fixture
def old_database():
    with db._connect() as conn:
        conn.executescript('''
            CREATE TABLE drops (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'new', parent_id INTEGER REFERENCES drops(id),
                brand_id INTEGER, created_at TEXT NOT NULL, registered_at TEXT);
            CREATE INDEX drops_brand ON drops(brand_id);
            CREATE TABLE drop_brand_history (id INTEGER PRIMARY KEY AUTOINCREMENT,
                drop_id INTEGER NOT NULL REFERENCES drops(id), brand_id INTEGER NOT NULL,
                brand_name TEXT NOT NULL, assigned_at TEXT NOT NULL, removed_at TEXT);
        ''')
        conn.executemany('INSERT INTO drops VALUES (?, ?, ?, ?, ?, ?, ?)', [
            (2, 'free.ru', 'new', None, None, '2020-01-01', None),
            (9, 'other-free.ru', 'used', None, None, '2020-02-01', '2019-01-01'),
            (15, 'parent.ru', 'in_use', None, 7, '2020-03-01', None),
            (20, 'child.ru', 'glued', 15, 7, '2020-04-01', None),
            (25, 'leaf.ru', 'glued', 20, 7, '2020-05-01', None),
            (31, 'changed.ru', 'used', None, 8, '2020-06-01', None),
            (40, 'removed.ru', 'new', None, None, '2020-07-01', None),
            (55, 'orphan.ru', 'used', 999, 8, '2020-08-01', None),
        ])
        conn.executemany('INSERT INTO drop_brand_history '
                         '(drop_id, brand_id, brand_name, assigned_at, removed_at) VALUES (?, ?, ?, ?, ?)', [
            (15, 7, 'Старое имя', '2021-01-01', None),
            (20, 7, 'Имя ребёнка', '2021-02-01', None),
            (31, 7, 'Закрытый первый', '2021-03-01', '2021-04-01'),
            (31, 8, 'Открытый второй', '2021-04-01', None),
            (40, 8, 'Удалённый бренд', '2021-05-01', '2021-06-01'),
            # Неконсистентная открытая история без активного бренда не создаёт назначение.
            (9, 8, 'Лишняя открытая', '2021-07-01', None),
        ])
        return [dict(r) for r in conn.execute('SELECT * FROM drops ORDER BY id')]


@pytest.mark.parametrize('reinitializations', [1, 2, 5])
def test_migration_preserves_data_and_is_idempotent(old_database, reinitializations):
    before_migration = datetime.now(timezone.utc).isoformat()
    db.init_db()
    after_migration = datetime.now(timezone.utc).isoformat()
    with db._connect() as conn:
        assert {r['name'] for r in conn.execute('PRAGMA table_info(drops)')} == {
            'id', 'domain', 'created_at', 'registered_at'}
        migrated = [dict(r) for r in conn.execute('SELECT * FROM drops ORDER BY id')]
        assert migrated == [{key: row[key] for key in migrated[0]} for row in old_database]
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='drop_brand_history'").fetchone()
        active = {r['drop_id']: dict(r) for r in conn.execute('SELECT * FROM drop_brands WHERE removed_at IS NULL')}
        assert set(active) == {15, 20, 25, 31, 55}
        for old in old_database:
            if old['brand_id'] is not None:
                assert active[old['id']]['brand_id'] == old['brand_id']
                assert active[old['id']]['status'] == old['status']
        assert active[20]['parent_id'] == active[15]['id']
        assert active[25]['parent_id'] == active[20]['id']
        assert active[55]['parent_id'] is None
        assert (active[15]['brand_name'], active[15]['assigned_at']) == ('Старое имя', '2021-01-01')
        assert (active[25]['brand_name'], active[25]['assigned_at']) == ('Бренд #7', '2020-05-01')
        closed = [dict(r) for r in conn.execute('SELECT * FROM drop_brands WHERE removed_at IS NOT NULL ORDER BY drop_id')]
        assert before_migration <= closed[0]['removed_at'] <= after_migration
        assert [(r['drop_id'], r['brand_id'], r['brand_name'], r['assigned_at'], r['removed_at']) for r in closed] == [
            (9, 8, 'Лишняя открытая', '2021-07-01', closed[0]['removed_at']),
            (31, 7, 'Закрытый первый', '2021-03-01', '2021-04-01'),
            (40, 8, 'Удалённый бренд', '2021-05-01', '2021-06-01')]
        assert all(r['status'] == 'used' and r['parent_id'] is None for r in closed)
    before = drops.history_for([r['id'] for r in old_database])
    for _ in range(reinitializations):
        db.init_db()
    assert drops.history_for([r['id'] for r in old_database]) == before
    assert add('next.ru') == 56
    with db._connect() as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_empty_legacy_and_fresh_database():
    with db._connect() as conn:
        conn.execute('CREATE TABLE drops (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT UNIQUE, '
                     'brand_id INTEGER, status TEXT, parent_id INTEGER, created_at TEXT, registered_at TEXT)')
    db.init_db()
    db.init_db()
    assert add() == 1


def test_fresh_database_init(app):
    db.init_db()
    db.init_db()
    assert rows() == {}
    with db._connect() as conn:
        assert 'brand_id' not in {r['name'] for r in conn.execute('PRAGMA table_info(drops)')}
        assert conn.execute('SELECT COUNT(*) FROM drop_brands').fetchone()[0] == 0


def test_migration_without_history_table():
    with db._connect() as conn:
        conn.execute('CREATE TABLE drops (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT UNIQUE, '
                     'brand_id INTEGER, status TEXT, parent_id INTEGER, created_at TEXT, registered_at TEXT)')
        conn.execute("INSERT INTO drops VALUES (183, 'legacy.ru', 7, 'in_use', NULL, '2020-01-01', NULL)")
    db.init_db()
    db.init_db()
    row = drops.history_for([183])[183][0]
    assert row['brand_name'] == 'Бренд #7' and row['assigned_at'] == '2020-01-01'
    assert row['status'] == 'in_use' and row['removed_at'] is None
    assert add() == 184


def test_migration_rolls_back_on_failure(old_database):
    with patch.object(db, '_migrate_drops', wraps=db._migrate_drops) as migrate:
        original = migrate._mock_wraps

        def fail(conn):
            original(conn)
            raise RuntimeError('after migration')

        migrate.side_effect = fail
        with pytest.raises(RuntimeError, match='after migration'):
            db.init_db()
    with db._connect() as conn:
        assert [dict(r) for r in conn.execute('SELECT * FROM drops ORDER BY id')] == old_database
        assert conn.execute('SELECT COUNT(*) FROM drop_brand_history').fetchone()[0] == 6
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='drop_brands'").fetchone()
    db.init_db()
    assert len(drops.brands_for_drops([r['id'] for r in old_database])) == 5


def test_multiple_brands_independent_status_and_glue(app):
    add('root.ru other.ru child.ru')
    ids = [r['id'] for r in rows().values()]
    report = drops.assign_brands(ids + ['bad', '9' * 100, ids[0]], TARGETS)
    assert all(r['assigned'] == 3 and r['already'] == 0 for r in report.values())
    first, second = brand_rows(7), brand_rows(8)
    drops.change_status(drops.brand_context_scope(7), [first['child.ru']['id']], 'glued', first['root.ru']['id'])
    drops.change_status(drops.brand_context_scope(8), [second['child.ru']['id']], 'glued', second['other.ru']['id'])
    drops.change_status(drops.brand_context_scope(7), [first['other.ru']['id']], 'in_use')
    assert brand_rows(8)['other.ru']['status'] == 'new'
    assert brand_rows(7)['child.ru']['parent_id'] == first['root.ru']['id']
    assert brand_rows(8)['child.ru']['parent_id'] == second['other.ru']['id']
    before = drops.history_for(ids)
    assert drops.assign_brands(ids, [(7, 'Новое имя')])[7] == {
        'brand_name': 'Новое имя', 'assigned': 0, 'already': 3}
    assert drops.history_for(ids) == before
    with db._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO drop_brands (drop_id, brand_id, brand_name, assigned_at) '
                         'VALUES (?, 7, ?, ?)', (ids[0], 'Дубль', '2026'))


def test_add_to_brand_reuses_drop_and_reports_tokens(app):
    item = add()
    added, skipped, report = drops.add_to_brand(7, 'Первый', 'one.ru ONE.RU bad')
    assert report == {7: dict(brand_name='Первый', assigned=1, already=0)}
    assert added == ['one.ru']
    assert skipped[0][1] == 'дубликат в списке'
    assert skipped[1][1].startswith('некорректный домен:')
    assert drops.add_to_brand(7, 'Первый', 'one.ru')[1] == [('one.ru', 'уже в этом бренде')]
    drops.add_to_brand(8, 'Второй', 'one.ru')
    assert rows()['one.ru']['id'] == item and len(rows()) == 1
    assert len(drops.history_for([item])[item]) == 2


def test_add_to_brand_report_counts_unique_existing_domains(app):
    add('one.ru', TARGETS[:1])
    added, skipped, report = drops.add_to_brand(7, 'Первый', 'one.ru ONE.RU two.ru bad')
    assert added == ['two.ru']
    assert [reason for _, reason in skipped[:2]] == ['уже в этом бренде', 'дубликат в списке']
    assert report == {7: dict(brand_name='Первый', assigned=1, already=1)}


@pytest.mark.parametrize('parent_id', [15, 999, None])
def test_migration_detaches_invalid_parent(old_database, parent_id):
    with db._connect() as conn:
        conn.execute("UPDATE drops SET status='glued', parent_id=? WHERE id=55", (parent_id,))
    db.init_db()
    child = drops.history_for([55])[55][0]
    assert child['brand_id'] == 8
    assert child['parent_id'] is None and child['status'] == 'used'


def test_migration_preserves_mismatched_open_history(old_database):
    with db._connect() as conn:
        conn.execute('INSERT INTO drop_brand_history '
                     '(drop_id, brand_id, brand_name, assigned_at) VALUES (55, 7, ?, ?)',
                     ('Чужое имя', '2021-08-01'))
    before = datetime.now(timezone.utc).isoformat()
    db.init_db()
    after = datetime.now(timezone.utc).isoformat()
    history = drops.history_for([55])[55]
    active = next(r for r in history if r['removed_at'] is None)
    closed = next(r for r in history if r['removed_at'] is not None)
    assert len(history) == 2
    assert (active['brand_id'], active['brand_name'], active['assigned_at']) == (8, 'Бренд #8', '2020-08-01')
    assert (closed['brand_id'], closed['brand_name'], closed['assigned_at']) == (7, 'Чужое имя', '2021-08-01')
    assert before <= closed['removed_at'] <= after
    assert closed['status'] == 'used' and closed['parent_id'] is None
    db.init_db()
    assert drops.history_for([55])[55] == history


def test_migration_preserves_open_history_without_active_brand(old_database):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    with patch.object(db, 'datetime') as clock:
        clock.now.return_value = now
        db.init_db()
    history = drops.history_for([9])[9]
    assert len(history) == 1
    row = history[0]
    assert (row['brand_id'], row['brand_name'], row['assigned_at']) == (8, 'Лишняя открытая', '2021-07-01')
    assert row['removed_at'] == now.isoformat()
    assert row['status'] == 'used' and row['parent_id'] is None
    assert drops.brands_for_drops([9]) == {}
    db.init_db()
    assert drops.history_for([9])[9] == history


def test_migration_preserves_duplicate_open_history(old_database):
    with db._connect() as conn:
        conn.execute('INSERT INTO drop_brand_history '
                     '(drop_id, brand_id, brand_name, assigned_at) VALUES (15, 7, ?, ?)',
                     ('Последнее имя', '2022-01-01'))
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    with patch.object(db, 'datetime') as clock:
        clock.now.return_value = now
        db.init_db()
    history = drops.history_for([15])[15]
    assert len(history) == 2
    active, closed = history
    assert (active['brand_name'], active['assigned_at'], active['removed_at']) == (
        'Последнее имя', '2022-01-01', None)
    assert (closed['brand_name'], closed['assigned_at'], closed['removed_at']) == (
        'Старое имя', '2021-01-01', now.isoformat())
    assert closed['status'] == 'used' and closed['parent_id'] is None
    db.init_db()
    assert drops.history_for([15])[15] == history


@pytest.mark.parametrize('ids', [[], ['bad', '9' * 100, 999]])
def test_unassign_empty_selection(app, ids):
    item = add(targets=TARGETS)
    before = drops.history_for([item])
    assert drops.unassign_brands(ids, [7, 8]) == {}
    assert drops.history_for([item]) == before


def test_remove_from_brand_reports_unique_known_periods(app):
    add('one.ru two.ru', TARGETS)
    first, second = brand_rows(), brand_rows(8)
    closed = first['one.ru']['id']
    active = first['two.ru']['id']
    assert drops.remove_from_brand(7, [closed]) == dict(removed=1, already_absent=0)
    ids = [closed, active, active, second['one.ru']['id'], 'bad', '9' * 100, 999]
    assert drops.remove_from_brand(7, ids) == dict(removed=1, already_absent=1)
    assert drops.remove_from_brand(7, ids) == dict(removed=0, already_absent=2)
    assert drops.remove_from_brand(7, []) == dict(removed=0, already_absent=0)
    assert brand_rows(8) == second


def test_remove_from_brand_rolls_back_child_detachment(app):
    add('root.ru child.ru', TARGETS)
    current = brand_rows()
    drops.change_status(drops.brand_context_scope(7), [current['child.ru']['id']],
                        'glued', current['root.ru']['id'])
    before = drops.history_for([r['id'] for r in rows().values()])
    with db._connect() as conn:
        conn.execute("CREATE TRIGGER reject_removal BEFORE UPDATE OF removed_at ON drop_brands "
                     "BEGIN SELECT RAISE(ABORT, 'removal failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match='removal failed'):
        drops.remove_from_brand(7, [current['root.ru']['id']])
    assert drops.history_for(list(before)) == before


def test_unassign_rolls_back_on_second_brand_failure(app):
    add('root.ru child.ru', TARGETS)
    for brand in (7, 8):
        current = brand_rows(brand)
        drops.change_status(drops.brand_context_scope(brand), [current['child.ru']['id']],
                            'glued', current['root.ru']['id'])
    ids = [r['id'] for r in rows().values()]
    before = drops.history_for(ids)
    with db._connect() as conn:
        conn.execute("CREATE TRIGGER reject_removal BEFORE UPDATE OF removed_at ON drop_brands "
                     "WHEN NEW.brand_id=8 BEGIN SELECT RAISE(ABORT, 'brand failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match='brand failed'):
        drops.unassign_brands([rows()['root.ru']['id']], [7, 8])
    assert drops.history_for(ids) == before
    assert 'root.ru' in brand_rows(7)


def test_missing_brand_context_title_without_registry_lookup(client, login, brands):
    brands.return_value = (BRANDS, None)
    login()
    with patch.object(drops, 'brands_for_drops') as lookup:
        response = client.get('/drops?brand=99')
    assert response.status_code == 200
    assert 'Дропы — Бренд #99' in response.text
    lookup.assert_not_called()


def test_reassign_opens_new_period(app):
    item = add(targets=TARGETS)
    before_second = brand_rows(8)
    report = drops.unassign_brands([item], [7, 7, 99])
    assert report[7]['removed'] == 1
    assert report[99]['already_absent'] == 1
    assert brand_rows(8) == before_second
    closed = drops.history_for([item], 7)[item][0]
    assert closed['removed_at'] and closed['brand_name'] == 'Первый'
    assert drops.unassign_brands([item], [7])[7]['already_absent'] == 1
    drops.assign_brands([item], [(7, 'Новое имя')])
    history = drops.history_for([item], 7)[item]
    assert len(history) == 2 and history[1] == closed
    assert history[0]['status'] == 'new' and history[0]['parent_id'] is None
    assert history[0]['removed_at'] is None and history[0]['brand_name'] == 'Новое имя'


@pytest.mark.parametrize('operation', ['remove', 'unassign', 'delete'])
def test_removing_parent_detaches_direct_children_only(app, operation):
    add('root.ru child.ru leaf.ru', TARGETS)
    for brand in (7, 8):
        current = brand_rows(brand)
        drops.change_status(drops.brand_context_scope(brand), [current['child.ru']['id']], 'glued', current['root.ru']['id'])
        drops.change_status(drops.brand_context_scope(brand), [current['leaf.ru']['id']], 'glued', current['child.ru']['id'])
    before_second = brand_rows(8)
    root = rows()['root.ru']['id']
    if operation == 'remove':
        assert drops.remove_from_brand(7, [brand_rows()['root.ru']['id']]) == dict(removed=1, already_absent=0)
    elif operation == 'unassign':
        assert drops.unassign_brands([root], [7])[7]['removed'] == 1
    else:
        assert drops.delete_domain([root, 'bad', '9' * 100]) == 1
        assert drops.history_for([root]) == {}
        assert root not in [r['id'] for r in rows().values()]
    after = brand_rows()
    assert after['child.ru']['parent_id'] is None and after['child.ru']['status'] == 'used'
    assert after['leaf.ru']['parent_id'] == after['child.ru']['id']
    if operation != 'delete':
        assert brand_rows(8) == before_second
    else:
        assert brand_rows(8)['child.ru']['parent_id'] is None
        with db._connect() as conn:
            assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_delete_parent_preserves_removed_child_status(app):
    root = add('root.ru child.ru', TARGETS[:1])
    child = rows()['child.ru']['id']
    current = brand_rows()
    drops.change_status(drops.brand_context_scope(7), [current['child.ru']['id']],
                        'glued', current['root.ru']['id'])
    assert drops.remove_from_brand(7, [current['child.ru']['id']]) == dict(removed=1, already_absent=0)
    closed = drops.history_for([child], 7)[child][0]
    assert closed['removed_at'] is not None
    assert closed['status'] == 'glued'
    assert closed['parent_id'] == current['root.ru']['id']

    assert drops.delete_domain([root]) == 1

    after = drops.history_for([child], 7)[child][0]
    assert after['status'] == closed['status']
    assert after['removed_at'] == closed['removed_at']
    with db._connect() as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_cross_brand_parent_and_foreign_ids_rejected(app):
    add('one.ru two.ru', TARGETS)
    first, second = brand_rows(7), brand_rows(8)
    before = drops.history_for([r['id'] for r in rows().values()])
    with pytest.raises(domains.DomainError, match='этому списку'):
        drops.change_status(drops.brand_context_scope(7), [first['one.ru']['id']], 'glued', second['two.ru']['id'])
    assert drops.change_status(drops.brand_context_scope(7), [second['one.ru']['id']], 'used') == 0
    assert drops.remove_from_brand(7, [second['one.ru']['id']]) == dict(removed=0, already_absent=0)
    assert drops.history_for(list(before)) == before


def test_unassign_multiple_brands_groups_parent_and_child(app):
    add('root.ru child.ru leaf.ru', TARGETS)
    ids = [rows()[name]['id'] for name in ('root.ru', 'child.ru')]
    for brand in (7, 8):
        current = brand_rows(brand)
        drops.change_status(drops.brand_context_scope(brand), [current['child.ru']['id']], 'glued', current['root.ru']['id'])
        drops.change_status(drops.brand_context_scope(brand), [current['leaf.ru']['id']], 'glued', current['child.ru']['id'])
    report = drops.unassign_brands(ids, [7, 8])
    assert all(r['removed'] == 2 and r['already_absent'] == 0 for r in report.values())
    for brand in (7, 8):
        current = brand_rows(brand)
        assert set(current) == {'leaf.ru'}
        assert current['leaf.ru']['parent_id'] is None and current['leaf.ru']['status'] == 'used'


def test_add_rolls_back_on_assignment_failure(app):
    with db._connect() as conn:
        conn.execute("CREATE TRIGGER reject_brand BEFORE INSERT ON drop_brands "
                     "WHEN NEW.brand_id=8 BEGIN SELECT RAISE(ABORT, 'brand failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match='brand failed'):
        drops.add_to_registry('one.ru two.ru', TARGETS)
    assert rows() == {}
    item = add()
    with pytest.raises(sqlite3.IntegrityError, match='brand failed'):
        drops.assign_brands([item], TARGETS)
    assert drops.history_for([item]) == {}
    with pytest.raises(sqlite3.IntegrityError, match='brand failed'):
        drops.add_to_brand(8, 'Второй', 'new.ru')
    assert set(rows()) == {'one.ru'}


@pytest.mark.parametrize('sort', ['created_at', 'registered_at'])
@pytest.mark.parametrize('order', ['asc', 'desc'])
def test_registry_sort_and_none(app, sort, order):
    add('a.ru b.ru c.ru')
    with db._connect() as conn:
        conn.execute("UPDATE drops SET created_at='2020', registered_at='2019' WHERE domain='a.ru'")
        conn.execute("UPDATE drops SET created_at='2021', registered_at='2018' WHERE domain='b.ru'")
        conn.execute("UPDATE drops SET created_at='2022' WHERE domain='c.ru'")
    ordered = drops.registry('all', sort, order)
    values = [r[sort] for r in ordered]
    nonnull = [v for v in values if v is not None]
    assert values == sorted(nonnull, reverse=order == 'desc') + [None] * values.count(None)
    drops.assign_brands([rows()['a.ru']['id']], TARGETS)
    assert {r['domain'] for r in drops.registry('none')} == {'b.ru', 'c.ru'}


@pytest.mark.parametrize('brand, title', [('all', 'все'), ('none', 'без бренда'), ('7', 'Первый')])
def test_add_redirect_preserves_current_filter(client, login, csrf_post, brand, title):
    login()
    response = csrf_post(f'/drops/domains?brand={brand}&sort=registered_at&dir=asc',
                         data={'domains': 'one.ru', 'brand_ids': ['7', '8']})
    brand_param = f'brand={brand}&' if brand != 'all' else ''
    assert response.location == f'/drops?{brand_param}sort=registered_at&dir=asc'
    page = client.get(response.location)
    assert f'Дропы — {title}' in page.text
    assert 'id="sidebar"' not in page.text
    assert page.headers['Cache-Control'] == 'no-store'
    item = rows()['one.ru']['id']
    expected = {7} if brand == '7' else {7, 8}
    assert {r['brand_id'] for r in drops.brands_for_drops([item])[item]} == expected
    body = page.text.split('<tbody>')[1].split('</tbody>')[0]
    if brand == 'none':
        assert 'one.ru' not in body
        assert 'Первый: присвоен 1 доменам' in page.text
        assert 'Второй: присвоен 1 доменам' in page.text
    else:
        assert 'one.ru' in body


def test_add_without_brands_and_existing_registry_domain(client, login, csrf_post, brands):
    login()
    response = csrf_post('/drops/domains?brand=none', data={'domains': 'HTTPS://Пример.РФ/path'})
    brands.assert_not_called()
    assert response.location.startswith('/drops?brand=none')
    assert 'пример.рф' in client.get(response.location).text
    item = rows()['xn--e1afmkfd.xn--p1ai']['id']
    csrf_post('/drops/domains', data={'domains': 'пример.рф', 'brand_ids': ['7', '8']})
    assert len(rows()) == 1
    assert len(drops.history_for([item])[item]) == 2
    csrf_post('/drops/domains', data={'domains': 'пример.рф', 'brand_ids': ['7', '8']})
    assert len(drops.history_for([item])[item]) == 2
    page = client.get('/drops').text
    assert 'уже были в бренде: 1' in page
    assert 'Добавлено доменов: 0.' in page


def test_registry_badges_live_names_and_closed_snapshots(client, login, brands):
    item = add(targets=[(7, 'Историческое имя')])
    drops.unassign_brands([item], [7])
    drops.assign_brands([item], [(7, 'Снимок активного'), (8, 'Снимок второго')])
    drops.change_status(drops.brand_context_scope(7), [brand_rows()['one.ru']['id']], 'used')
    brands.return_value = ([{'id': 7, 'name': 'Живое имя'}], None)
    login()
    page = client.get('/drops').text
    assert 'Историческое имя' in page and 'Живое имя' in page and 'Бренд #8' in page
    assert 'Снимок активного' not in page and 'Снимок второго' not in page
    assert 'pill--danger' in page and '<details class="domain-history">' in page
    assert 'по настоящее время' in page
    assert 'data-chip-group="status"' in page and 'name="parent_id"' not in page
    assert '<select name="status"' not in page
    assert 'value="status"' not in page
    assert 'data-confirm="Удалить выбранные домены и всю историю во всех брендах?"' in page
    assert 'name="remove_brand_ids" value="8"' in page
    closed = drops.history_for([item])[item][-1]
    assert client.application.jinja_env.filters['fmt_datetime'](closed['removed_at']) in page


@pytest.mark.parametrize('path', ['/brands/7/drops', '/drops?brand=7'])
def test_brand_context_history_and_independent_rows(client, login, path):
    add(targets=[(7, 'Старый первый'), (8, 'Чужая история')])
    item = rows()['one.ru']['id']
    drops.unassign_brands([item], [7, 8])
    drops.add_to_brand(7, 'Первый', 'one.ru')
    login()
    page = client.get(path).text
    assert 'Старый первый' in page and 'Чужая история' not in page
    assert 'Снять с бренда' in page and 'name="status"' in page
    assert 'name="brand_ids"' not in page
    assert 'name="ids" value="' + str(brand_rows()['one.ru']['id']) + '"' in page


@pytest.mark.parametrize('brand, expected', [
    ('all', {'first.ru', 'second.ru', 'free.ru'}), ('none', {'free.ru'}),
    ('7', {'first.ru'}), ('8', {'second.ru'}), ('invalid', {'first.ru', 'second.ru', 'free.ru'}),
])
def test_index_filters(client, login, brand, expected):
    add('first.ru', TARGETS[:1])
    add('second.ru', TARGETS[1:])
    add('free.ru')
    login()
    body = client.get('/drops?brand=' + brand, follow_redirects=True).text.split('<tbody>')[1].split('</tbody>')[0]
    for name in ('first.ru', 'second.ru', 'free.ru'):
        assert (name in body) == (name in expected)


@pytest.mark.parametrize('path', ['/brands/7/drops/domains', '/drops/domains?brand=7'])
def test_brand_add_existing_drop_and_noop(client, login, csrf_post, path):
    item = add(targets=[(8, 'Второй')])
    login()
    csrf_post(path, data={'domains': 'one.ru'})
    response = csrf_post(path, data={'domains': 'one.ru'})
    page = client.get(response.location).text
    assert 'уже в этом бренде' in page and 'уже были в бренде: 1' in page
    assert len(rows()) == 1 and len(drops.history_for([item])[item]) == 2


@pytest.mark.parametrize('path', ['/brands/7/drops/domains/bulk', '/drops/domains/bulk?brand=7'])
@pytest.mark.parametrize('action', ['delete', 'status', 'assign_brands', 'unassign_brands'])
def test_context_foreign_ids_ignored(client, login, csrf_post, path, action):
    item = add(targets=TARGETS)
    foreign = brand_rows(8)['one.ru']['id']
    before = drops.history_for([item])
    login()
    response = csrf_post(path, data={'ids': [foreign], 'action': action, 'status': 'used',
                                     'target_brand_ids': ['7'], 'remove_brand_ids': ['8']})
    assert response.status_code == 302
    assert drops.history_for([item]) == before


@pytest.mark.parametrize('path', ['/brands/7/drops/domains/bulk', '/drops/domains/bulk?brand=7'])
def test_context_foreign_parent_rejected(client, login, csrf_post, path):
    item = add(targets=TARGETS)
    own, foreign = brand_rows(7)['one.ru']['id'], brand_rows(8)['one.ru']['id']
    before = drops.history_for([item])
    login()
    response = csrf_post(path, data={'ids': [own], 'action': 'status', 'status': 'glued', 'parent_id': foreign})
    assert 'Родитель должен принадлежать этому списку' in client.get(response.location).text
    assert drops.history_for([item]) == before


@pytest.mark.parametrize('path', ['/brands/7/drops/domains/bulk', '/drops/domains/bulk?brand=7'])
def test_context_remove_and_repeat_report(client, login, csrf_post, path):
    item = add(targets=TARGETS)
    row_id = brand_rows()['one.ru']['id']
    login()
    response = csrf_post(path, data={'ids': [row_id], 'action': 'delete'})
    assert 'Первый: снят у 1 доменов, не было в бренде: 0' in client.get(response.location).text
    response = csrf_post(path, data={'ids': [row_id], 'action': 'delete'})
    assert 'Первый: снят у 0 доменов, не было в бренде: 1' in client.get(response.location).text
    assert [r['brand_id'] for r in drops.brands_for_drops([item])[item]] == [8]


def test_registry_bulk_reports_and_full_delete(client, login, csrf_post):
    item = add()
    login()
    path = '/drops/domains/bulk?brand=all&sort=registered_at&dir=asc'
    for already in (0, 1):
        response = csrf_post(path, data={'ids': [item], 'action': 'assign_brands', 'target_brand_ids': ['7', '8']})
        assert response.location == '/drops?sort=registered_at&dir=asc'
        page = client.get(response.location).text
        for name in ('Первый', 'Второй'):
            assert f'{name}: присвоен {1 - already} доменам, уже были в бренде: {already}' in page
    for absent in (0, 1):
        response = csrf_post(path, data={'ids': [item], 'action': 'unassign_brands', 'remove_brand_ids': ['7']})
        assert f'Первый: снят у {1 - absent} доменов, не было в бренде: {absent}' in client.get(response.location).text
    csrf_post(path, data={'ids': [item], 'action': 'delete'})
    assert rows() == {} and drops.history_for([item]) == {}


@pytest.mark.parametrize('action', ['delete', 'assign_brands', 'unassign_brands', 'status'])
def test_none_filter_isolation(client, login, csrf_post, action):
    item = add(targets=TARGETS)
    before = drops.history_for([item])
    login()
    csrf_post('/drops/domains/bulk?brand=none', data={'ids': [item], 'action': action,
                                                    'target_brand_ids': ['7'], 'remove_brand_ids': ['7']})
    assert len(rows()) == 1 and drops.history_for([item]) == before


@pytest.mark.parametrize('raw', ['bad', '-1', '９', '9' * 100, '99'])
def test_invalid_add_brand_preserves_database(client, login, csrf_post, raw):
    login()
    response = csrf_post('/drops/domains', data={'domains': 'one.ru', 'brand_ids': [raw]})
    assert response.status_code == 302 and rows() == {}


def test_brand_service_error_blocks_assign_but_allows_remove(client, login, csrf_post, brands):
    item = add(targets=TARGETS)
    brands.return_value = ([], 'Brand недоступен')
    login()
    response = csrf_post('/drops/domains/bulk', data={'ids': [item], 'action': 'assign_brands', 'target_brand_ids': ['7']})
    page = client.get(response.location).text
    assert 'Присвоение брендов недоступно' in page and 'value="assign_brands" disabled' in page
    assert len(drops.history_for([item])[item]) == 2
    csrf_post('/drops/domains/bulk', data={'ids': [item], 'action': 'unassign_brands', 'remove_brand_ids': ['7']})
    assert len(drops.brands_for_drops([item])[item]) == 1


@pytest.mark.parametrize('engine', ['yandex', 'google'])
def test_engine_regression(client, login, csrf_post, engine):
    scope = domains.brand_engine_scope(7, engine)
    domains.add_domains(scope, 'one.ru two.ru')
    ids = [r['id'] for r in domains.list_tree(scope)]
    item = add(targets=TARGETS)
    before = drops.history_for([item])
    login()
    path = f'/brands/7/{engine}/domains/bulk'
    csrf_post(path, data={'ids': ids, 'action': 'status', 'status': 'used'})
    assert {r['status'] for r in domains.list_tree(scope)} == {'used'}
    csrf_post(path, data={'ids': ids, 'action': 'delete'})
    assert domains.list_tree(scope) == []
    assert drops.history_for([item]) == before


@pytest.mark.parametrize('path', ['/drops/domains', '/drops/domains?brand=7', '/drops/domains/bulk',
                                  '/drops/domains/bulk?brand=7', '/brands/7/drops/domains',
                                  '/brands/7/drops/domains/bulk'])
def test_post_protection(client, csrf_post, login, path):
    assert client.post(path).status_code == 400
    assert csrf_post(path).location == '/login'
    login()
    assert client.post(path).status_code == 400


@pytest.mark.parametrize('path', ['/drops', '/drops?brand=7', '/brands/7/drops'])
def test_login_required(client, brands, path):
    assert client.get(path).location == '/login'
    brands.assert_not_called()
