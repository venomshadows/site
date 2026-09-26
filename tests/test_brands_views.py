import re
from unittest.mock import patch

import pytest
import requests

from site_app import api_client, brand_client, db
from site_app.brands_views import TABS

BRANDS = [{'id': 7, 'name': 'Первый', 'query_frequency_total': 123},
          {'id': 8, 'name': 'Второй'}]


@pytest.mark.parametrize('tab', ['yandex', 'google'])
@pytest.mark.parametrize('sort', ['created_at', 'status', 'registered_at'])
@pytest.mark.parametrize('order, aria', [('asc', 'ascending'), ('desc', 'descending')])
def test_domain_sort_headers(client, login, tab, sort, order, aria):
    login()
    with patch.object(brand_client, 'list_brands', return_value=(BRANDS, None)):
        page = client.get(f'/brands/7/{tab}?sort={sort}&dir={order}')
    assert page.status_code == 200
    head = page.text.split('<thead>')[1].split('</thead>')[0]
    for field in ('created_at', 'status', 'registered_at'):
        direction = 'desc' if field == sort and order == 'asc' else 'asc'
        assert f'href="/brands/7/{tab}?sort={field}&amp;dir={direction}"' in head
    assert head.count('aria-sort="none"') == 2
    assert f'aria-sort="{aria}"' in head
    assert '<select name="sort">' not in page.text
    assert 'Выбрано: <span data-selection-count>0</span>' in page.text


def test_domain_sort_headers_default_and_invalid_query(client, login):
    login()
    with patch.object(brand_client, 'list_brands', return_value=(BRANDS, None)):
        default = client.get('/brands/7/yandex').text
        invalid = client.get('/brands/7/yandex?sort=DROP&dir=oops', follow_redirects=True).text
    for page in (default, invalid):
        head = page.split('<thead>')[1].split('</thead>')[0]
        assert 'sort=created_at&amp;dir=asc' in head
        assert 'aria-sort="descending"' in head


def test_index_redirects_to_first_brand(client, login):
    login()
    with patch.object(brand_client, 'list_brands', return_value=(BRANDS, None)):
        assert client.get('/').location == '/brands/7/yandex'


@pytest.mark.parametrize('error, expected', [(None, 'Пока нет брендов'), ('Ошибка Brand', 'Ошибка Brand')])
def test_index_empty_and_error(client, login, error, expected):
    login()
    with patch.object(brand_client, 'list_brands', return_value=([], error)):
        response = client.get('/')
    assert response.status_code == 200 and expected in response.text


@pytest.mark.parametrize('tab', TABS)
def test_tabs(client, login, tab):
    login()
    with patch.object(brand_client, 'list_brands', return_value=(BRANDS, None)):
        response = client.get(f'/brands/7/{tab}')
    assert response.status_code == 200
    assert f'<h1>{TABS[tab]["title"]}</h1>' in response.text
    assert 'Список доменов пуст.' in response.text
    assert 'name="domains"' in response.text
    assert 'class="domain-status ' not in response.text
    assert 'Раздел в разработке.' not in response.text
    assert '(123)' in response.text and '()' not in response.text
    assert 'aria-label="Бренды"' in response.text
    sidebar = re.search(r'<nav aria-label="Бренды">.*?</nav>', response.text, re.S)
    assert sidebar is not None
    assert '<a href="/brands/7/yandex" aria-current="page">' in sidebar.group()
    assert '<a href="/brands/8/yandex">' in sidebar.group()
    for key in TABS:
        assert f'/brands/7/{key}' in response.text
    for logo in ('yandex', 'google'):
        assert f'/_ui/img/{logo}.svg?v=' in response.text
        assert client.get(f'/_ui/img/{logo}.svg').status_code == 200


@pytest.mark.parametrize('path', ['/brands/7/unknown-tab', '/brands/99/yandex'])
def test_unknown_brand_or_tab(client, login, path):
    login()
    with patch.object(brand_client, 'list_brands', return_value=(BRANDS, None)):
        assert client.get(path).status_code == 404


def test_brand_http_timeout_renders_error_on_pages(client, login):
    db.update_settings(brand_api_key='test-brand-key')
    login()
    with patch.object(api_client.requests, 'get', side_effect=requests.Timeout) as fetch:
        for path in ('/', '/brands/1/yandex'):
            fetch.reset_mock()
            response = client.get(path)
            fetch.assert_called_once()
            assert response.status_code == 200
            assert 'Brand недоступен: Timeout' in response.text


def test_brand_unavailable(client, login):
    login()
    with patch.object(brand_client, 'list_brands', return_value=([], 'Ошибка Brand')):
        response = client.get('/brands/7/yandex')
    assert response.status_code == 200
    assert 'Brand недоступен' in response.text and 'Ошибка Brand' in response.text
    assert '<nav class="subnav"' not in response.text


@pytest.mark.parametrize('path', ['/', *(f'/brands/7/{tab}' for tab in TABS), '/brands/7/unknown-tab'])
def test_login_required(client, path):
    with patch.object(brand_client, 'list_brands') as fetch:
        assert client.get(path).location == '/login'
    fetch.assert_not_called()
