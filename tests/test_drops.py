"""История дропов, перенос веток и изоляция форм."""
from unittest.mock import patch
import sqlite3

import pytest

from site_app import brand_client, db, domains, drops

BRANDS = [{'id': 7, 'name': 'Первый'}, {'id': 8, 'name': 'Второй'}]


@pytest.fixture(autouse=True)
def brands():
    with patch.object(brand_client, 'list_brands', return_value=(BRANDS, None)) as fetch:
        yield fetch


def rows():
    return {r['domain']: r for r in drops.list_tree(drops.all_scope('all'))}


def add(text='one.ru', brand=None):
    drops.add_domains(drops.add_scope(brand), text)
    return rows()[text.split()[0]]['id']


@pytest.mark.parametrize('path', ['/drops/domains', '/brands/7/drops/domains'])
def test_add_with_brand_opens_history(client, login, csrf_post, brands, path):
    login()
    response = csrf_post(path, data={'domains': 'HTTPS://ONE.ru/path one.ru', 'brand_id': '7'})
    assert response.status_code == 302
    brands.assert_called_once_with()
    item = rows()['one.ru']
    assert item['brand_id'] == 7
    assert drops.history_for([item['id']])[item['id']] == [{
        'drop_id': item['id'], 'brand_name': BRANDS[0]['name'],
        'assigned_at': item['created_at'], 'removed_at': None,
    }]
    brands.return_value = ([{'id': 7, 'name': 'Новое имя'}], None)
    csrf_post(path, data={'domains': 'one.ru', 'brand_id': '7'})
    history = drops.history_for([item['id']])[item['id']]
    assert len(history) == 1 and history[0]['brand_name'] == BRANDS[0]['name']


def test_add_without_brand_has_no_history_or_brand_request(client, login, csrf_post, brands):
    login()
    assert csrf_post('/drops/domains', data={'domains': 'free.ru'}).status_code == 302
    item = rows()['free.ru']
    assert item['brand_id'] is None
    assert drops.history_for([item['id']]) == {}
    brands.assert_not_called()


def test_add_missing_brand_preserves_addition_without_history(client, login, csrf_post, brands):
    login()
    assert csrf_post('/drops/domains', data={'domains': 'one.ru', 'brand_id': '99'}).status_code == 302
    item = rows()['one.ru']
    assert item['brand_id'] == 99
    assert drops.history_for([item['id']]) == {}
    brands.assert_called_once_with()


def test_add_history_failure_rolls_back_drop(app):
    with db._connect() as conn:
        conn.execute("""CREATE TRIGGER reject_history BEFORE INSERT ON drop_brand_history
            BEGIN SELECT RAISE(ABORT, 'history failed'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='history failed'):
        drops.add_domains(drops.add_scope(7), 'one.ru two.ru', brand_name=BRANDS[0]['name'])
    assert rows() == {}
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM drop_brand_history').fetchone()[0] == 0


def test_history_lifecycle(app):
    item = add()
    scope = drops.all_scope('all')
    assert drops.assign_brand(scope, [item], 7, 'Снимок имени') == 1
    first = drops.history_for([item])[item][0]
    assert first['brand_name'] == 'Снимок имени'
    assert first['assigned_at'].endswith('+00:00') and first['removed_at'] is None
    assert drops.assign_brand(scope, [item], 7, 'Новое имя') == 0
    assert drops.history_for([item])[item] == [first]
    assert drops.assign_brand(scope, [item], 8, 'Второй') == 1
    history = drops.history_for([item])[item]
    assert [h['brand_name'] for h in history] == ['Второй', 'Снимок имени']
    assert history[0]['removed_at'] is None
    assert history[1]['removed_at'] == history[0]['assigned_at'] >= first['assigned_at']
    assert drops.unassign_brand(scope, [item]) == 1
    assert rows()['one.ru']['brand_id'] is None
    history = drops.history_for([item])[item]
    assert len(history) == 2 and all(h['removed_at'] for h in history)
    assert drops.unassign_brand(scope, [item]) == 0
    assert drops.history_for([]) == {}


@pytest.fixture
def tree(app):
    add('root.ru child.ru leaf.ru', 7)
    ids = {name: row['id'] for name, row in rows().items()}
    scope = drops.all_scope('all')
    domains.change_status(scope, [ids['child.ru']], 'glued', ids['root.ru'])
    domains.change_status(scope, [ids['leaf.ru']], 'glued', ids['child.ru'])
    return ids


def test_transfer_subtree_and_overlapping_selection(tree):
    scope = drops.brand_scope(7)
    assert drops.assign_brand(scope, [tree['root.ru'], tree['child.ru']], 8, 'Второй') == 3
    assert {r['brand_id'] for r in rows().values()} == {8}
    assert rows()['leaf.ru']['parent_id'] == tree['child.ru']
    assert all(len(h) == 1 for h in drops.history_for(list(tree.values())).values())
    assert drops.unassign_brand(drops.brand_scope(8), [tree['root.ru']]) == 3
    assert {r['brand_id'] for r in rows().values()} == {None}
    assert all(h[0]['removed_at'] for h in drops.history_for(list(tree.values())).values())


def test_transfer_skips_unchanged_descendant_and_reads_full_tree(tree):
    with db._connect() as conn:
        conn.execute('UPDATE drops SET brand_id=8 WHERE id=?', (tree['leaf.ru'],))
        conn.execute(
            'INSERT INTO drop_brand_history (drop_id, brand_id, brand_name, assigned_at) '
            'SELECT id, brand_id, ?, created_at FROM drops WHERE id=?',
            (BRANDS[1]['name'], tree['leaf.ru']))
    history = drops.history_for([tree['leaf.ru']])
    assert drops.assign_brand(drops.brand_scope(7), [tree['root.ru']], 8, 'Второй') == 2
    assert drops.history_for([tree['leaf.ru']]) == history
    assert rows()['leaf.ru']['parent_id'] == tree['child.ru']


@pytest.mark.parametrize('parent_brand', [7, 8])
def test_assign_same_brand_repairs_missing_history_without_detaching(tree, parent_brand):
    # Фикстура добавляет дропы без имени бренда, поэтому история ещё не открыта.
    with db._connect() as conn:
        conn.execute('UPDATE drops SET brand_id=? WHERE id=?', (parent_brand, tree['root.ru']))
    before = rows()
    assert drops.history_for(list(tree.values())) == {}
    scope = drops.brand_scope(7)
    assert drops.assign_brand(scope, [tree['child.ru']], 7, 'Имя бренда') == 2
    assert rows() == before
    history = drops.history_for(list(tree.values()))
    assert set(history) == {tree['child.ru'], tree['leaf.ru']}
    for entries in history.values():
        assert len(entries) == 1
        assert entries[0]['brand_name'] == 'Имя бренда'
        assert entries[0]['removed_at'] is None
    assert drops.assign_brand(scope, [tree['child.ru']], 7, 'Имя бренда') == 0
    assert drops.history_for(list(tree.values())) == history
    assert rows() == before


@pytest.mark.parametrize('brand_name', [None, ''])
def test_assign_missing_brand_name_preserves_database(tree, brand_name):
    scope = drops.all_scope('all')
    drops.assign_brand(scope, [tree['root.ru']], 7, BRANDS[0]['name'])
    before = rows()
    history = drops.history_for(list(tree.values()))
    with pytest.raises(domains.DomainError, match='Не удалось определить имя бренда для истории'):
        drops.assign_brand(scope, [tree['child.ru']], 8, brand_name)
    assert rows() == before
    assert drops.history_for(list(tree.values())) == history


@pytest.mark.parametrize('parent_brand', [7, 8])
def test_assign_same_brand_preserves_glue_and_history(tree, parent_brand):
    scope = drops.all_scope('all')
    drops.assign_brand(scope, [tree['root.ru']], 8, BRANDS[1]['name'])
    # Нарушаем инвариант напрямую: повторное присвоение не должно чинить клей.
    with db._connect() as conn:
        conn.execute('UPDATE drops SET brand_id=? WHERE id=?', (parent_brand, tree['root.ru']))
    before = rows()
    history = drops.history_for(list(tree.values()))
    assert drops.assign_brand(drops.brand_scope(8), [tree['child.ru']], 8, 'Новое имя') == 0
    assert rows() == before
    assert rows()['child.ru']['parent_id'] == tree['root.ru']
    assert rows()['child.ru']['status'] == 'glued'
    assert drops.history_for(list(tree.values())) == history


@pytest.mark.parametrize('target_brand', [8, None])
def test_transfer_to_parent_brand_preserves_glue(tree, target_brand):
    with db._connect() as conn:
        conn.execute('UPDATE drops SET brand_id=? WHERE id=?', (target_brand, tree['root.ru']))
    if target_brand is None:
        assert drops.unassign_brand(drops.brand_scope(7), [tree['child.ru']]) == 2
    else:
        assert drops.assign_brand(drops.brand_scope(7), [tree['child.ru']], target_brand, BRANDS[1]['name']) == 2
    current = rows()
    assert {row['brand_id'] for row in current.values()} == {target_brand}
    assert current['child.ru']['parent_id'] == tree['root.ru']
    assert current['child.ru']['status'] == 'glued'
    assert current['leaf.ru']['parent_id'] == tree['child.ru']
    assert current['leaf.ru']['status'] == 'glued'


def test_transfer_without_parent_detaches_selected_branch(tree):
    assert drops.assign_brand(drops.brand_scope(7), [tree['child.ru']], 8, 'Второй') == 2
    current = rows()
    assert current['root.ru']['brand_id'] == 7
    assert current['child.ru']['parent_id'] is None and current['child.ru']['status'] == 'new'
    assert current['leaf.ru']['parent_id'] == tree['child.ru']
    assert current['leaf.ru']['status'] == 'glued'


def test_glue_brand_rule(app):
    a = add('a.ru', 7)
    b = add('b.ru', 8)
    scope = drops.all_scope('all')
    with pytest.raises(domains.DomainError, match='другого бренда'):
        domains.change_status(scope, [a], 'glued', b)
    c = add('c.ru')
    e = add('e.ru')
    assert domains.change_status(scope, [e], 'glued', c) == 1
    assert rows()['e.ru']['parent_id'] == c


def test_delete_removes_history_and_detaches_children(tree):
    scope = drops.all_scope('all')
    drops.assign_brand(scope, [tree['root.ru']], 8, 'Второй')
    assert drops.delete_domains(scope, [tree['root.ru']]) == 1
    assert drops.history_for([tree['root.ru']]) == {}
    assert rows()['child.ru']['status'] == 'used'
    assert rows()['child.ru']['parent_id'] is None
    assert len(drops.history_for([tree['child.ru']])[tree['child.ru']]) == 1


def test_delete_detaches_child_outside_scope(app):
    drops.add_domains(drops.brand_scope(7), 'parent.ru', brand_name=BRANDS[0]['name'])
    drops.add_domains(drops.brand_scope(8), 'child.ru', brand_name=BRANDS[1]['name'])
    parent = rows()['parent.ru']['id']
    child = rows()['child.ru']['id']
    history = drops.history_for([child])
    # Синтетический клей между брендами: штатная смена статуса его запрещает.
    with db._connect() as conn:
        conn.execute("UPDATE drops SET parent_id=?, status='glued' WHERE id=?", (parent, child))
    assert drops.delete_domains(drops.brand_scope(7), [parent, child]) == 1
    current = rows()
    assert set(current) == {'child.ru'}
    assert current['child.ru']['parent_id'] is None
    assert current['child.ru']['status'] == 'used'
    assert current['child.ru']['brand_id'] == 8
    assert drops.history_for([parent, child]) == history


@pytest.mark.parametrize('engine', ['yandex', 'google'])
def test_domain_bulk_status_and_delete(client, login, csrf_post, engine):
    scope = domains.brand_engine_scope(7, engine)
    domains.add_domains(scope, 'one.ru two.ru')
    ids = [row['id'] for row in domains.list_tree(scope)]
    drop_id = add()
    drops.assign_brand(drops.all_scope('all'), [drop_id], 7, BRANDS[0]['name'])
    drop_rows = rows()
    history = drops.history_for([drop_id])
    login()
    path = f'/brands/7/{engine}/domains/bulk'
    response = csrf_post(path, data={'ids': ids, 'action': 'status', 'status': 'used'})
    assert response.status_code == 302
    assert {row['status'] for row in domains.list_tree(scope)} == {'used'}
    response = csrf_post(path, data={'ids': ids, 'action': 'delete'})
    assert response.status_code == 302
    assert domains.list_tree(scope) == []
    assert rows() == drop_rows
    assert drops.history_for([drop_id]) == history


@pytest.mark.parametrize('brand, expected', [
    ('all', {'one.ru', 'two.ru', 'free.ru'}), ('none', {'free.ru'}),
    ('7', {'one.ru'}), ('8', {'two.ru'}), ('invalid', {'one.ru', 'two.ru', 'free.ru'}),
])
def test_index_filters(client, login, brand, expected):
    add('one.ru', 7)
    add('two.ru', 8)
    add('free.ru')
    login()
    page = client.get('/drops?brand=' + brand)
    assert page.status_code == 200 and page.headers['Cache-Control'] == 'no-store'
    assert 'id="sidebar"' not in page.text
    for name in ('one.ru', 'two.ru', 'free.ru'):
        assert (name in page.text) == (name in expected)
    assert 'name="brand_id"' in page.text and 'История брендов' in page.text


@pytest.mark.parametrize('brand_id', ['7', 'none'])
def test_add_redirect_uses_form_brand_and_preserves_sort(client, login, csrf_post, brand_id):
    login()
    response = csrf_post('/drops/domains?brand=8&sort=status&order=asc',
                         data={'domains': 'added.ru', 'brand_id': brand_id})
    assert response.status_code == 302
    assert response.location == f'/drops?brand={brand_id}&sort=status&order=asc'
    page = client.get(response.location)
    assert page.status_code == 200
    assert 'added.ru' in page.text.split('<tbody>')[1].split('</tbody>')[0]


def test_add_normalization_uniqueness_and_brand_tab(client, login, csrf_post):
    login()
    response = csrf_post('/drops/domains?brand=none&sort=status&order=asc',
                         data={'domains': 'HTTPS://Пример.РФ/path'})
    assert response.location == '/drops?brand=none&sort=status&order=asc'
    assert rows()['xn--e1afmkfd.xn--p1ai']['brand_id'] is None
    assert 'пример.рф' in client.get(response.location).text
    csrf_post('/drops/domains', data={'domains': 'brand.ru', 'brand_id': '7'})
    csrf_post('/brands/8/drops/domains', data={'domains': 'brand.ru own.ru'})
    current = rows()
    assert current['brand.ru']['brand_id'] == 7 and current['own.ru']['brand_id'] == 8
    assert len(current) == 3
    page = client.get('/brands/8/drops').text
    assert 'уже есть в списке' in page and 'own.ru' in page
    assert 'brand.ru' not in page.split('<tbody>')[1].split('</tbody>')[0]
    assert 'История брендов' in page and 'name="brand_id"' not in page
    assert 'value="assign_brand"' in page


@pytest.mark.parametrize('raw', ['', 'oops', '-1', '９', '9' * 100])
def test_invalid_add_brand_is_unassigned(client, login, csrf_post, raw):
    login()
    assert csrf_post('/drops/domains', data={'domains': 'one.ru', 'brand_id': raw}).status_code == 302
    assert rows()['one.ru']['brand_id'] is None


@pytest.mark.parametrize('path', ['/brands/7/drops/domains/bulk', '/drops/domains/bulk?brand=7',
                                  '/drops/domains/bulk?brand=none'])
@pytest.mark.parametrize('action', ['status', 'delete', 'assign_brand', 'unassign_brand'])
def test_bulk_foreign_ids_ignored(client, login, csrf_post, path, action):
    item = add('foreign.ru', 8)
    login()
    before = rows()
    response = csrf_post(path, data={'ids': [item], 'action': action, 'status': 'used', 'target_brand_id': '7'})
    assert response.status_code == 302 and rows() == before
    assert drops.history_for([item]) == {}


@pytest.mark.parametrize('path', ['/drops/domains/bulk', '/brands/7/drops/domains/bulk'])
def test_assign_routes_and_brand_error(client, login, csrf_post, brands, path):
    item = add('one.ru', 7)
    login()
    brands.return_value = ([], 'Brand недоступен: Timeout')
    with patch.object(drops, 'assign_brand') as assign:
        response = csrf_post(path, data={'ids': [item], 'action': 'assign_brand', 'target_brand_id': '8'})
        assign.assert_not_called()
    assert 'Присвоение бренда недоступно' in client.get(response.location).text
    assert rows()['one.ru']['brand_id'] == 7
    csrf_post(path, data={'ids': [item], 'action': 'unassign_brand'})
    assert rows()['one.ru']['brand_id'] is None


def test_bulk_actions_and_history_markup(client, login, csrf_post):
    item = add()
    login()
    path = '/drops/domains/bulk?brand=all&sort=status&order=asc'
    response = csrf_post(path, data={'ids': [item], 'action': 'assign_brand', 'target_brand_id': '7'})
    assert response.location == '/drops?brand=all&sort=status&order=asc'
    history = drops.history_for([item])[item][0]
    page = client.get(response.location).text
    assert '<details>' in page and 'по настоящее время' in page
    assert 'brand=all&amp;sort=created_at&amp;order=desc' in page
    assert 'name="brand" value="all"' in page
    assert 'Перенесено дропов: 1.' in page and history['brand_name'] == 'Первый'
    assert client.application.jinja_env.filters['fmt_datetime'](history['assigned_at']) in page
    csrf_post('/brands/7/drops/domains/bulk',
              data={'ids': [item], 'action': 'assign_brand', 'target_brand_id': '8'})
    assert rows()['one.ru']['brand_id'] == 8
    csrf_post(path, data={'ids': [item], 'action': 'status', 'status': 'used'})
    assert rows()['one.ru']['status'] == 'used'
    csrf_post(path, data={'ids': [item], 'action': 'delete'})
    assert rows() == {} and drops.history_for([item]) == {}


def test_missing_brand_and_disabled_assignment(client, login, csrf_post, brands):
    item = add('one.ru', 99)
    login()
    response = csrf_post('/drops/domains/bulk',
                         data={'ids': [item], 'action': 'assign_brand', 'target_brand_id': '99'})
    page = client.get(response.location).text
    assert 'Бренд не найден' in page and 'Бренд #99' in page
    brands.return_value = ([], 'Brand недоступен')
    page = client.get('/drops').text
    assert 'value="assign_brand" disabled' in page
    assert 'value="unassign_brand">' in page
    assert rows()['one.ru']['brand_id'] == 99


@pytest.mark.parametrize('path', ['/drops/domains', '/drops/domains/bulk',
                                  '/brands/7/drops/domains', '/brands/7/drops/domains/bulk'])
def test_post_protection(client, csrf_post, login, path):
    assert client.post(path).status_code == 400
    assert csrf_post(path).location == '/login'
    login()
    assert client.post(path).status_code == 400


def test_login_required(client, brands):
    assert client.get('/drops').location == '/login'
    brands.assert_not_called()
