"""Доменные списки, атомарность массовых операций и защита маршрутов."""
from unittest.mock import patch

import pytest

from site_app import brand_client, db, domains as d


@pytest.mark.parametrize('raw, expected', [
    (' HTTP://Example.RU:8080/path?q=1#f ', 'example.ru'),
    ('https://EXAMPLE.ru.', 'example.ru'), ('ftp://example.ru?q=1', 'example.ru'),
    ('пример.рф', 'xn--e1afmkfd.xn--p1ai'),
    ('xn--e1afmkfd.xn--p1ai', 'xn--e1afmkfd.xn--p1ai'),
])
def test_normalize(raw, expected):
    assert d.normalize_domain(raw) == expected
    assert d.normalize_domain(d.display_domain(expected)) == expected


@pytest.mark.parametrize('raw', ['', ' ', 'localhost', 'a..ru', '-a.ru', 'a_.ru',
                                     'a@b.ru', 'a b.ru', 'https:///path', 'a.ru:bad',
                                     'xn--.ru', 'a' * 64 + '.ru', '💩.ru'])
def test_invalid_domain(raw):
    with pytest.raises(d.DomainError, match='.+'):
        d.normalize_domain(raw)


def rows(brand=7, engine='yandex'):
    return {r['domain']: r for r in d.list_tree(brand, engine)}


@pytest.fixture
def tree(app):
    d.add_domains(7, 'yandex', 'a.ru b.ru c.ru d.ru e.ru')
    ids = {name[0]: row['id'] for name, row in rows().items()}
    d.change_status(7, 'yandex', [ids['b']], 'glued', ids['a'])
    d.change_status(7, 'yandex', [ids['c']], 'glued', ids['b'])
    d.change_status(7, 'yandex', [ids['d']], 'glued', ids['c'])
    return ids


def test_add_and_isolation(app):
    d.add_domains(7, 'yandex', 'old.ru')
    added, skipped = d.add_domains(7, 'yandex', 'one.ru, two.ru\nONE.RU https://old.ru/path мусор')
    assert added == ['one.ru', 'two.ru']
    assert [reason for _, reason in skipped][:2] == ['дубликат в списке', 'уже есть в списке']
    assert skipped[2][1].startswith('некорректный домен:')
    assert d.add_domains(7, 'google', 'one.ru')[0] == ['one.ru']
    assert d.add_domains(8, 'yandex', 'one.ru')[0] == ['one.ru']
    row = rows()['one.ru']
    assert row['status'] == 'new' and row['registered_at'] is None
    assert row['created_at'].endswith('+00:00')


def test_detach_preserves_children(tree):
    assert d.change_status(7, 'yandex', [tree['b']], 'in_use') == 1
    current = rows()
    assert current['b.ru']['parent_id'] is None
    assert current['b.ru']['status'] == 'in_use'
    assert current['c.ru']['parent_id'] == tree['b']
    assert current['c.ru']['depth'] == 1


@pytest.mark.parametrize('selected, parent', [(['a'], 'a'), (['a', 'e'], 'd'), (['a', 'b'], 'b')])
def test_glue_rejected_atomically(tree, selected, parent):
    before = rows()
    with pytest.raises(d.DomainError):
        d.change_status(7, 'yandex', [tree[x] for x in selected], 'glued', tree[parent])
    assert rows() == before


@pytest.mark.parametrize('parent', [None, '', 'bad', 99999])
def test_parent_required(tree, parent):
    before = rows()
    with pytest.raises(d.DomainError):
        d.change_status(7, 'yandex', [tree['e']], 'glued', parent)
    assert rows() == before


def test_delete_parent(tree):
    assert d.delete_domains(7, 'yandex', [tree['a']]) == 1
    current = rows()
    assert 'a.ru' not in current
    assert current['b.ru']['status'] == 'used' and current['b.ru']['parent_id'] is None
    assert current['c.ru']['parent_id'] == tree['b']
    assert current['d.ru']['parent_id'] == tree['c']


def test_delete_parent_and_child(tree):
    assert d.delete_domains(7, 'yandex', [tree['a'], tree['b']]) == 2
    current = rows()
    assert set(current) == {'c.ru', 'd.ru', 'e.ru'}
    assert current['c.ru']['status'] == 'used' and current['c.ru']['parent_id'] is None
    assert current['d.ru']['parent_id'] == tree['c']


@pytest.mark.parametrize('operation', ['status', 'delete', 'glued'])
def test_foreign_ids_ignored(tree, operation):
    for brand, engine in [(8, 'yandex'), (7, 'google')]:
        d.add_domains(brand, engine, 'a.ru b.ru')
    foreign = [*rows(8).values(), *rows(7, 'google').values()]
    ids = [r['id'] for r in foreign] + [tree['e'], 'bad', '999999999999999999999999999']
    if operation == 'delete':
        assert d.delete_domains(7, 'yandex', ids) == 1
    else:
        assert d.change_status(7, 'yandex', ids, 'glued' if operation == 'glued' else 'used', tree['a']) == 1
    assert foreign == [*rows(8).values(), *rows(7, 'google').values()]
    with pytest.raises(d.DomainError):
        d.change_status(7, 'yandex', [tree['a']], 'glued', foreign[0]['id'])


@pytest.mark.parametrize('brand, engine', [(8, 'yandex'), (7, 'google')])
def test_change_status_scopes_update_without_selected_filter(tree, brand, engine):
    before = rows()
    with patch.object(d, '_selected', return_value=[tree['b']]):
        d.change_status(brand, engine, [tree['b']], 'used')
    assert rows() == before


@pytest.mark.parametrize('sort', d.SORTS)
@pytest.mark.parametrize('order', ['asc', 'desc'])
def test_sibling_sorting(app, sort, order):
    d.add_domains(7, 'yandex', 'root.ru other.ru child.ru sibling.ru null.ru')
    current = rows()
    ids = {name: r['id'] for name, r in current.items()}
    with db._connect() as conn:
        for name, date, status, registered, parent in [
            ('root.ru', '2025-01-01', 'used', '2025-01-01', None),
            ('other.ru', '2025-02-01', 'new', '2025-02-01', None),
            ('child.ru', '2025-03-01', 'glued', '2025-03-01', ids['root.ru']),
            ('sibling.ru', '2024-01-01', 'in_use', None, ids['root.ru']),
            ('null.ru', '2025-04-01', 'in_use', None, None),
        ]:
            conn.execute('UPDATE domains SET created_at=?, status=?, registered_at=?, parent_id=? WHERE id=?',
                         (date, status, registered, parent, ids[name]))
    result = d.list_tree(7, 'yandex', sort, order)
    root_index = next(i for i, r in enumerate(result) if r['domain'] == 'root.ru')
    children = result[root_index + 1:root_index + 3]
    assert {r['domain'] for r in children} == {'child.ru', 'sibling.ru'}
    assert [r['depth'] for r in children] == [1, 1]
    roots = [r for r in result if r['depth'] == 0]
    for group in (roots, children):
        values = [r[sort] for r in group]
        if sort == 'status':
            values = [list(d.STATUSES).index(v) for v in values]
        nonnull = [v for v in values if v is not None]
        assert nonnull == sorted(nonnull, reverse=order == 'desc')
        if sort == 'registered_at':
            assert values == nonnull + [None] * values.count(None)


@pytest.mark.parametrize('suffix', ['', '/bulk'])
def test_post_protection(client, csrf_post, suffix):
    path = '/brands/7/yandex/domains' + suffix
    assert client.post(path).status_code == 400
    assert csrf_post(path).location == '/login'


@pytest.mark.parametrize('engine', ['drops', 'unknown'])
@pytest.mark.parametrize('suffix', ['', '/bulk'])
def test_post_unknown_engine(client, login, csrf_post, engine, suffix):
    login()
    assert csrf_post(f'/brands/7/{engine}/domains{suffix}').status_code == 404


def test_add_redirect_flash_and_unicode(client, login, csrf_post):
    login()
    with patch.object(brand_client, 'list_brands', side_effect=AssertionError('POST must be local')):
        response = csrf_post('/brands/7/yandex/domains?sort=status&order=asc',
                             data={'domains': 'пример.рф пример.рф bad'})
    assert response.location == '/brands/7/yandex?sort=status&order=asc'
    with patch.object(brand_client, 'list_brands', return_value=([{'id': 7, 'name': 'Бренд'}], None)):
        page = client.get(response.location)
        assert 'Добавлено доменов: 1.' in page.text and 'дубликат в списке' in page.text
        assert 'некорректный домен' in page.text and 'пример.рф' in page.text
        assert 'domain-status--new' in page.text
        assert page.headers['Cache-Control'] == 'no-store'
        assert 'Добавлено доменов' not in client.get(response.location).text
        assert client.get('/brands/7/yandex?sort=DROP&order=oops').status_code == 200
    assert d.sorting('DROP', 'oops') == ('created_at', 'desc')


@pytest.mark.parametrize('data, message', [
    ({'action': 'wrong'}, 'Неизвестное действие'),
    ({'action': 'status', 'status': 'wrong'}, 'Неизвестный статус'),
    ({'action': 'status', 'status': 'glued'}, 'Выберите родительский домен'),
])
def test_bulk_errors(client, login, csrf_post, tree, data, message):
    login()
    before = rows()
    response = csrf_post('/brands/7/yandex/domains/bulk?sort=status&order=asc',
                         data={**data, 'ids': [tree['a'], tree['e']]})
    assert response.location == '/brands/7/yandex?sort=status&order=asc'
    assert rows() == before
    with client.session_transaction() as session:
        assert session['_flashes'][-1][0] == 'error'
        assert message in session['_flashes'][-1][1]


def test_bulk_routes(client, login, csrf_post, tree):
    login()
    path = '/brands/7/yandex/domains/bulk'
    assert csrf_post(path, data={'action': 'status', 'ids': [tree['a'], tree['e']], 'status': 'used'}).status_code == 302
    assert rows()['a.ru']['status'] == rows()['e.ru']['status'] == 'used'
    assert csrf_post(path, data={'action': 'delete', 'ids': [tree['a'], tree['e']]}).status_code == 302
    assert set(rows()) == {'b.ru', 'c.ru', 'd.ru'}


def test_domain_tree_selection_markup(client, login, tree):
    login()
    with patch.object(brand_client, 'list_brands', return_value=([{'id': 7, 'name': 'Бренд'}], None)):
        page = client.get('/brands/7/yandex').text
    for row in d.list_tree(7, 'yandex'):
        modifier = ' domain-name--nested' if row['depth'] > 0 else ''
        assert f'class="domain-name{modifier}" style="--depth: {row["depth"]}"' in page
        assert f'name="ids" value="{row["id"]}"' in page
    assert page.count('data-domains-all') == 1
    assert page.count('data-domains-count') == 1
    assert 'role="status" aria-live="polite"' in page
    assert 'Выбрано: <span data-domains-count>0</span>' in page
