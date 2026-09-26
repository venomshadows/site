"""Фильтры считаются сервером; контекст клея не становится совпадением."""
from copy import deepcopy
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from site_app import brand_client, domains, drops


def sample_rows():
    return [
        dict(id=1, domain='root.ru', status='used', parent_id=None, depth=0),
        dict(id=2, domain='middle.ru', status='glued', parent_id=1, depth=1),
        dict(id=3, domain='xn--e1afmkfd.xn--p1ai', status='glued', parent_id=2, depth=2),
        dict(id=4, domain='sibling.ru', status='new', parent_id=1, depth=1),
        dict(id=5, domain='other.ru', status='in_use', parent_id=None, depth=0),
    ]


@pytest.mark.parametrize('q', ['ПРИМЕР', 'пример.рф', 'XN--E1AFMKFD', 'xn--p1ai', '  ПрИмЕр  '])
def test_idn_search_keeps_ancestors_without_siblings(q):
    rows = sample_rows()
    before = deepcopy(rows)
    result = domains.list_filters(rows, q, 'glued')
    assert [row['id'] for row in result['domain_rows']] == [1, 2, 3]
    assert [row['depth'] for row in result['domain_rows']] == [0, 1, 2]
    assert result['status_counts'] == {'': 1, 'new': 0, 'in_use': 0, 'used': 0, 'glued': 1}
    assert (result['shown'], result['total']) == (3, 5)
    assert result['q'] == q.strip()
    assert rows == before


@pytest.mark.parametrize('status,expected', [('', [1, 2, 3, 4, 5]), ('new', [1, 4]),
                                          ('used', [1]), ('in_use', [5]), ('glued', [1, 2, 3])])
def test_status_counts_ignore_only_status_filter(status, expected):
    result = domains.list_filters(sample_rows(), status=status)
    assert [row['id'] for row in result['domain_rows']] == expected
    assert result['status_counts'] == {'': 5, 'new': 1, 'in_use': 1, 'used': 1, 'glued': 2}


def test_no_match_and_cycle_terminate():
    assert domains.list_filters(sample_rows(), 'missing')['domain_rows'] == []
    rows = sample_rows()[:2]
    rows[0]['parent_id'] = 2
    assert len(domains.list_filters(rows, 'middle')['domain_rows']) == 2


def test_registry_counts_distinct_domains_across_active_brand_statuses():
    rows = [dict(id=1, domain='one.ru'), dict(id=2, domain='two.ru'), dict(id=3, domain='free.ru')]
    active = {1: [{'status': 'new'}, {'status': 'used'}, {'status': 'new'}], 2: [{'status': 'used'}]}
    result = drops.registry_filters(rows, active, status='new')
    assert [row['id'] for row in result['domain_rows']] == [1]
    assert result['status_counts'] == {'': 3, 'new': 1, 'in_use': 0, 'used': 2, 'glued': 0}
    assert drops.registry_filters(rows, active, q='two')['status_counts']['used'] == 1


@pytest.fixture
def lists(client, monkeypatch):
    with client.session_transaction() as session:
        session['stage'] = 2
    monkeypatch.setattr(brand_client, 'list_brands', lambda: ([{'id': 7, 'name': 'Первый'}, {'id': 8, 'name': 'Второй'}], None))
    text = 'root.ru пример.рф sibling.ru'
    for engine in ('yandex', 'google'):
        scope = domains.brand_engine_scope(7, engine)
        domains.add_domains(scope, text)
        rows = {row['domain']: row for row in domains.list_tree(scope)}
        domains.change_status(scope, [rows['xn--e1afmkfd.xn--p1ai']['id']], 'glued', rows['root.ru']['id'])
    drops.add_to_registry(text, [(7, 'Первый')])
    scope = drops.brand_context_scope(7)
    rows = {row['domain']: row for row in drops.list_tree(scope)}
    drops.change_status(scope, [rows['xn--e1afmkfd.xn--p1ai']['id']], 'glued', rows['root.ru']['id'])
    drops.add_to_registry('free.ru')
    drops.add_to_registry('other.ru', [(8, 'Второй')])


LIST_PATHS = ['/brands/7/yandex', '/brands/7/google', '/brands/7/drops', '/drops?brand=7', '/drops']


@pytest.mark.parametrize('path', LIST_PATHS)
def test_every_list_uses_server_filters_and_tree_context(client, lists, path):
    assert ('data-list-filter' in client.get(path).text) == path.startswith('/drops')
    separator = '&' if '?' in path else '?'
    page = client.get(path + separator + 'q=ПРИМЕР&status=glued&sort=created_at&dir=asc')
    assert page.status_code == 200
    table = page.text.split('<tbody>')[1].split('</tbody>')[0]
    assert 'пример.рф' in table
    assert ('root.ru' in table) == (path != '/drops')
    assert 'sibling.ru' not in table
    assert page.text.count('data-mode="server"') == 2
    assert 'data-filter-bar' in page.text and 'data-chip-group="status"' in page.text
    assert 'data-bulk-bar' in page.text
    assert 'name="ids"' in table
    assert 'aria-sort="ascending"' in page.text
    assert 'domain_sort_form' not in page.text and '<select name="sort"' not in page.text
    assert 'data-list-count' in page.text and 'Сбросить' in page.text
    links = re.findall(r'<a class="th-sort" href="([^"]+)"', page.text)
    for link in links:
        query = parse_qs(urlsplit(unescape(link)).query)
        assert query['q'] == ['ПРИМЕР'] and query['status'] == ['glued']
        assert 'dir' in query and 'order' not in query
    # Родитель может не совпадать с фильтром, но доступен для новой склейки.
    if path != '/drops':
        parent = re.search(r'<select name="parent_id".*?</select>', page.text, re.S).group()
        assert 'sibling.ru' in parent


@pytest.mark.parametrize('brand,expected,total', [('none', ['free.ru'], 1), ('7', ['root.ru', 'пример.рф', 'sibling.ru'], 3), ('8', ['other.ru'], 1), ('all', ['root.ru', 'пример.рф', 'sibling.ru', 'free.ru', 'other.ru'], 5)])
def test_drop_brand_filter(client, lists, brand, expected, total):
    page = client.get('/drops?brand=' + brand).text
    body = page.split('<tbody>')[1].split('</tbody>')[0]
    for name in ['root.ru', 'пример.рф', 'sibling.ru', 'free.ru', 'other.ru']:
        assert (name in body) == (name in expected)
    assert f'{total} из {total}' in page
    assert 'data-list-filter' in page


@pytest.mark.parametrize('path', LIST_PATHS)
@pytest.mark.parametrize('direction', [None, 'desc'])
def test_legacy_order_canonicalized_once_and_dir_wins(client, lists, path, direction):
    query = '&' if '?' in path else '?'
    response = client.get(path + query + 'sort=created_at&order=asc&q=root' + ('&dir=' + direction if direction else ''))
    assert response.status_code == 302
    parsed = parse_qs(urlsplit(response.location).query)
    assert parsed['dir'] == [direction or 'asc'] and 'order' not in parsed
    assert parsed['q'] == ['root']
    page = client.get(response.location)
    assert page.status_code == 200
    assert f'data-dir="{direction or "asc"}"' in page.text


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            assert self.current is None, 'Вложенная форма'
            self.current = dict(attrs=attrs, inputs=[])
            self.forms.append(self.current)
        elif tag == 'input' and self.current is not None:
            self.current['inputs'].append(attrs)

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


@pytest.mark.parametrize('path', LIST_PATHS + ['/settings', '/login', '/login2'])
def test_forms_have_csrf_and_bulk_checkboxes_submit_ids(client, lists, path):
    if path in ('/login', '/login2'):
        with client.session_transaction() as session:
            session['stage'] = 0 if path == '/login' else 1
    response = client.get(path)
    assert response.status_code == 200
    parser = FormParser()
    parser.feed(response.text)
    assert parser.forms
    for form in parser.forms:
        if form['attrs'].get('method') == 'post':
            assert any(field.get('name') == 'csrf_token' and field['value'] for field in form['inputs'])
            query = parse_qs(urlsplit(form['attrs'].get('action', '')).query, keep_blank_values=True)
            assert 'q' not in query and 'status' not in query
    if path in LIST_PATHS:
        bulk = next(form for form in parser.forms if '/bulk?' in form['attrs'].get('action', ''))
        selected = [field for field in bulk['inputs'] if 'data-row-select' in field]
        assert selected and all(field['name'] == 'ids' and field['value'].isdigit() for field in selected)
        assert all('name' not in field for field in bulk['inputs'] if 'data-select-all' in field)


def test_drop_brand_groups_use_package_markup_and_preserve_values(client, lists):
    page = client.get('/drops').text
    groups = re.findall(r'<fieldset[^>]*class="checkbox-group".*?</fieldset>', page, re.S)
    assert len(groups) == 3
    assert page.count('<details class="checkbox-menu">') == 2
    assert 'aria-describedby="brand_ids-group-hint"' in groups[0]
    assert 'Ничего не выбирайте, чтобы добавить без брендов.' in groups[0]
    for group, name in zip(groups, ('brand_ids', 'target_brand_ids', 'remove_brand_ids')):
        assert 'checkbox-group__options' in group
        parser = FormParser()
        parser.feed('<form>' + group + '</form>')
        inputs = parser.forms[0]['inputs']
        assert {(field['name'], field['value']) for field in inputs} == {(name, '7'), (name, '8')}
        assert all(field['type'] == 'checkbox' for field in inputs)
    assert 'name="action" value="assign_brands"' in page
    assert 'name="action" value="unassign_brands"' in page
    templates = Path(client.application.root_path) / 'templates'
    for file in templates.glob('*.html'):
        source = file.read_text(encoding='utf-8')
        assert '<fieldset' not in source
        assert 'brand_checkboxes' not in source


def test_all_pages_resolve_to_shared_layout_and_assets(client, lists):
    for path in LIST_PATHS + ['/', '/settings', '/login', '/login2']:
        if path in ('/login', '/login2'):
            with client.session_transaction() as session:
                session['stage'] = 0 if path == '/login' else 1
        response = client.get(path, follow_redirects=path == '/')
        assert response.status_code == 200
        page = response.text
        parser = FormParser()
        parser.feed(page)
        assert parser.forms
        assert '/_ui/ui.css' in page and '/_ui/list.js' in page
        assert 'fonts.googleapis.com' not in page and '/static/img/clouds.svg' not in page
    templates = Path(client.application.root_path) / 'templates'
    for name in ('base.html', 'app_shell.html', 'auth_base.html', '_topbar.html'):
        assert not (templates / name).exists()
    for file in templates.glob('*.html'):
        source = file.read_text(encoding='utf-8')
        assert 'domain_sort_form' not in source and 'sort_link' not in source
        assert '<select name="sort"' not in source and '<!doctype' not in source


@pytest.mark.parametrize('path', LIST_PATHS)
def test_form_urls_omit_empty_filters_and_validate_status(client, lists, path):
    separator = '&' if '?' in path else '?'
    page = client.get(path + separator + 'status=invalid&q=   ', follow_redirects=True).text
    parser = FormParser()
    parser.feed(page)
    actions = [form['attrs']['action'] for form in parser.forms
               if '/domains' in form['attrs'].get('action', '')]
    assert len(actions) == 2
    for action in actions:
        query = parse_qs(urlsplit(action).query, keep_blank_values=True)
        assert 'q' not in query and 'status' not in query
    assert 'Ничего не найдено.' not in page


@pytest.mark.parametrize('q', ['ROOT', 'straße', '  ПрИмЕр  '])
def test_search_input_preserves_user_text(client, lists, q):
    page = client.get('/drops', query_string={'q': q}, follow_redirects=True).text
    assert f'value="{q.strip()}"' in page


def test_search_reuses_display_domain(monkeypatch):
    rows = [dict(id=1, domain='xn--e1afmkfd.xn--p1ai', display_domain='пример.рф')]
    def unexpected_decode(domain):
        pytest.fail('Prepared display_domain must be reused')
    monkeypatch.setattr(domains, 'display_domain', unexpected_decode)
    assert domains.list_filters(rows, 'ПРИМЕР')['domain_rows'] == rows


@pytest.mark.parametrize('path', LIST_PATHS)
@pytest.mark.parametrize('bulk', [False, True])
def test_post_redirect_preserves_list_filters(client, lists, csrf_post, path, bulk):
    parsed = urlsplit(path)
    query = dict(parse_qs(parsed.query), sort='registered_at', dir='asc', q='ROOT', status='glued')
    endpoint = parsed.path + '/domains' + ('/bulk' if bulk else '')
    response = csrf_post(endpoint, query_string=query,
                         data={'action': 'delete'} if bulk else {'domains': 'added.ru'})
    assert response.status_code == 302
    redirected = parse_qs(urlsplit(response.location).query)
    for key in ('sort', 'dir', 'q', 'status'):
        assert redirected[key] == [query[key]]
    if 'brand' in query:
        assert redirected['brand'] == query['brand']


@pytest.mark.parametrize('path,expected', [
    ('/drops?dir=&order=asc', {'sort': ['created_at'], 'dir': ['asc']}),
    ('/drops?dir=&dir=asc&order=desc', {'sort': ['created_at'], 'dir': ['asc']}),
    ('/brands/7/yandex?sort=&sort=status', {'sort': ['status'], 'dir': ['desc']}),
    ('/drops?sort=status&dir=asc', {'sort': ['created_at'], 'dir': ['asc']}),
    ('/drops?order=asc&sort=status&brand=nope&q=%20x%20',
     {'sort': ['created_at'], 'dir': ['asc'], 'q': ['x']}),
    ('/brands/7/yandex?q=%20%20ROOT%20%20&status=nope',
     {'sort': ['created_at'], 'dir': ['desc'], 'q': ['ROOT']}),
    ('/drops?sort=unknown&dir=bad&extra=one&extra=two',
     {'sort': ['created_at'], 'dir': ['desc'], 'extra': ['one', 'two']}),
])
def test_canonical_redirect_normalizes_once(client, lists, path, expected):
    response = client.get(path)
    assert response.status_code == 302
    assert urlsplit(response.location).path == urlsplit(path).path
    assert parse_qs(urlsplit(response.location).query) == expected
    page = client.get(response.location)
    assert page.status_code == 200
    hidden = re.search(r'<input[^>]*name="sort"[^>]*value="([^"]+)"', page.text)
    table = re.search(r'data-sort="([^"]+)"', page.text)
    assert hidden.group(1) == table.group(1)


@pytest.mark.parametrize('path', LIST_PATHS)
@pytest.mark.parametrize('suffix', ['', 'sort=created_at&dir=asc&q=root&status=new'])
def test_canonical_and_bare_lists_do_not_redirect(client, lists, path, suffix):
    response = client.get(path + (('&' if '?' in path else '?') + suffix if suffix else ''))
    assert response.status_code == 200


@pytest.mark.parametrize('brand', [None, 'all', 'none', '7'])
@pytest.mark.parametrize('bulk', [False, True])
def test_drop_urls_use_canonical_brand_filter(client, lists, csrf_post, brand, bulk):
    query = {'brand': brand} if brand else {}
    parser = FormParser()
    parser.feed(client.get('/drops', query_string=query).text)
    actions = [form['attrs']['action'] for form in parser.forms
               if '/drops/domains' in form['attrs'].get('action', '')]
    assert len(actions) == 2
    response = csrf_post('/drops/domains' + ('/bulk' if bulk else ''),
                         query_string=query,
                         data={'action': 'delete'} if bulk else {'domains': 'added.ru'})
    assert response.status_code == 302
    for url in actions + [response.location]:
        params = parse_qs(urlsplit(url).query, keep_blank_values=True)
        if brand in (None, 'all'):
            assert 'brand' not in params
        else:
            assert params['brand'] == [brand]
    assert client.get(response.location).status_code == 200


@pytest.mark.parametrize('path', LIST_PATHS)
def test_list_ids_unique_and_parent_initially_hidden(client, lists, path):
    page = client.get(path).text
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert ids.count('domains') == 1
    assert ids.count('domain-table') == 1
    assert 'data-target="domains"' not in page
    assert page.count('data-target="domain-table"') == 2
    if path != '/drops':
        assert re.search(r'<div\s+data-domain-parent\s+hidden>', page)
