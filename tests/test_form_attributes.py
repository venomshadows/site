"""Атрибуты форм до миграции сохраняют валидацию и подсказки браузера."""
from html.parser import HTMLParser

import pytest

from site_app import brand_client, db


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.ids = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        if tag in ('input', 'textarea', 'select') and 'name' in attrs:
            self.fields[attrs['name']] = attrs


@pytest.mark.parametrize('path,stage,expected', [
    *[(path, 2, {'domains': {'required': None, 'rows': '3',
                           'placeholder': 'example.ru, пример.рф'}})
      for path in ('/brands/7/yandex', '/brands/7/google', '/brands/7/drops',
                   '/drops', '/drops?brand=7')],
    ('/login', 0, {
        'username': {'type': 'text', 'placeholder': 'Логин', 'required': None,
                     'autofocus': 'True', 'autocomplete': 'username'},
        'password': {'type': 'password', 'placeholder': 'Пароль', 'required': None,
                     'autocomplete': 'current-password'},
    }),
    ('/login2', 1, {
        'password': {'type': 'password', 'placeholder': 'Второй пароль',
                     'required': None, 'autofocus': 'True', 'autocomplete': 'one-time-code'},
    }),
    ('/settings', 2, {
        'notify_email': {'type': 'email', 'placeholder': 'admin@example.com', 'autocomplete': 'off'},
        'smtp_host': {'type': 'text', 'placeholder': 'localhost', 'autocomplete': 'off'},
        'smtp_port': {'type': 'number', 'min': '1', 'max': '65535', 'autocomplete': 'off'},
        'smtp_username': {'type': 'text', 'placeholder': 'без авторизации, если пусто', 'autocomplete': 'off'},
        'smtp_password': {'type': 'password', 'value': '', 'autocomplete': 'new-password',
                          'placeholder': '•••••• (сохранён)'},
        'smtp_from': {'type': 'text', 'placeholder': 'site@site.venomshadows.ru', 'autocomplete': 'off'},
        'smtp_use_tls': {'type': 'checkbox', 'value': '1', 'autocomplete': 'off'},
        'telegram_bot_token': {'type': 'text', 'placeholder': '123456789:AA…', 'autocomplete': 'off', 'value': 'visible-token'},
        'telegram_chat_id': {'type': 'text', 'placeholder': 'например, 123456789', 'autocomplete': 'off'},
        'brand_api_key': {'type': 'text', 'placeholder': 'Ключ ещё не задан', 'autocomplete': 'off', 'value': 'visible-key'},
    }),
])
def test_rendered_form_attributes(client, monkeypatch, path, stage, expected):
    with client.session_transaction() as session:
        session['stage'] = stage
    monkeypatch.setattr(brand_client, 'list_brands', lambda: ([{'id': 7, 'name': 'Бренд'}], None))
    db.update_settings(smtp_password='must-not-render', telegram_bot_token='visible-token',
                       brand_api_key='visible-key')
    response = client.get(path)
    assert response.status_code == 200
    assert 'must-not-render' not in response.text
    controls = Controls(response.text)
    for name, attributes in expected.items():
        actual = controls.fields[name]
        for attribute, value in attributes.items():
            assert attribute in actual, (path, name, attribute)
            if attribute not in ('required', 'autofocus'):
                assert actual[attribute] == value, (path, name, attribute)
        if name == 'domains':
            assert actual['aria-describedby'] in controls.ids
