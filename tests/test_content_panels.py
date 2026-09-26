from html.parser import HTMLParser
from unittest.mock import patch

import pytest

from site_app import brand_client


class PanelContents(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.panels = 0
        self.contents = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        panel = tag == 'section' and 'panel' in attrs.get('class', '').split()
        self.panels += panel
        inside = panel or any(self.stack)
        if inside:
            self.contents.append((tag, attrs))
        if tag not in {'input', 'img', 'link', 'meta', 'br', 'hr'}:
            self.stack.append(inside)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()


@pytest.mark.parametrize('path', ['/brands/7/yandex', '/brands/7/google',
                                  '/brands/7/drops', '/drops', '/drops?brand=7'])
def test_page_content_inside_single_panel(client, login, path):
    login()
    with patch.object(brand_client, 'list_brands', return_value=([
        {'id': 7, 'name': 'Example', 'query_frequency_total': 123}
    ], None)):
        response = client.get(path)
    assert response.status_code == 200
    panel = PanelContents(response.text)
    assert panel.panels == 1
    assert any(tag == 'h1' for tag, _ in panel.contents)
    for expected in ('domain-add', 'filter-bar', 'bulk-bar'):
        assert any(expected in attrs.get('class', '').split() for _, attrs in panel.contents)
    assert any(tag == 'table' for tag, _ in panel.contents)


@pytest.mark.parametrize('path,error', [('/', None), ('/', 'Brand unavailable'),
                                       ('/brands/7/yandex', 'Brand unavailable')])
def test_empty_state_inside_panel(client, login, path, error):
    login()
    with patch.object(brand_client, 'list_brands', return_value=([], error)):
        response = client.get(path)
    assert response.status_code == 200
    panel = PanelContents(response.text)
    assert panel.panels == 1
    assert any('empty-state' in attrs.get('class', '').split() for _, attrs in panel.contents)


@pytest.mark.parametrize('frequency', [0, 123, None])
def test_sidebar_name_and_accessible_frequency(client, login, frequency):
    login()
    with patch.object(brand_client, 'list_brands', return_value=([
        {'id': 7, 'name': 'Example', 'query_frequency_total': frequency}
    ], None)):
        response = client.get('/brands/7/yandex')
    assert '<span class="brand-name">Example</span>' in response.text
    if frequency is None:
        assert 'class="brand-frequency"' not in response.text
    else:
        assert '<span class="brand-frequency" title="' in response.text
        frequency_span = response.text.split('<span class="brand-frequency"', 1)[1].split('>', 1)[0]
        assert 'aria-hidden' not in frequency_span
        assert f'>({frequency})</span>' in response.text
