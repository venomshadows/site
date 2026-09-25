import re
from unittest.mock import Mock

import pytest

from site_app import brand_client, db, notifications


@pytest.mark.parametrize('path,active', [('/', 'Бренды'), ('/brands/7/yandex', 'Бренды'), ('/drops', 'Дропы'), ('/settings', 'Настройки')])
def test_shared_navigation(client, login, monkeypatch, path, active):
    login()
    monkeypatch.setattr(brand_client, 'list_brands', lambda: ([{'id': 7, 'name': 'Brand'}], None) if path != '/' else ([], None))
    html = client.get(path).text
    header = re.search(r'<header class="topbar">(.*?)</header>', html, re.S).group(1)
    assert re.findall(r'class="topbar__tab[^\"]*"[^>]*>(.*?)</a>', header) == ['Бренды', 'Дропы', 'Настройки']
    assert f'aria-current="page">{active}</a>' in header
    assert header.count('aria-current="page"') == 1
    assert 'method="post" action="/logout" class="topbar__logout"' in header
    assert 'name="csrf_token"' in header
    assert html.count('action="/logout"') == 1
    assert 'sidebar__account' not in html and 'btn-link' not in header


STORED = dict(notify_email='keep@example.com', smtp_host='smtp.example.com', smtp_port=587,
              smtp_username='user', smtp_password='secret', smtp_from='from@example.com',
              smtp_use_tls=1, telegram_bot_token='token', telegram_chat_id='42', brand_api_key='brand')


@pytest.mark.parametrize('section,updates', [
    ('email', {'notify_email': 'new@example.com', 'smtp_host': 'new.host', 'smtp_use_tls': 0}),
    ('telegram', {'telegram_bot_token': 'new-token', 'telegram_chat_id': '43'}),
    ('brand', {'brand_api_key': 'new-brand'}),
])
@pytest.mark.parametrize('endpoint', ['/settings', '/settings/test-email', '/settings/test-telegram'])
def test_section_updates_preserve_other_values(client, login, csrf_post, monkeypatch, section, updates, endpoint):
    login()
    db.update_settings(**STORED)
    for sender in ('send_email', 'send_telegram'):
        monkeypatch.setattr(notifications, sender, Mock(return_value=notifications.SendResult(ok=True)))
    data = {'section': section, **{key: f'  {value}  ' for key, value in updates.items() if key != 'smtp_use_tls'}}
    assert csrf_post(endpoint, data=data, follow_redirects=True).status_code == 200
    actual = dict(db.get_settings())
    for key, value in (STORED | updates).items():
        assert actual[key] == value


@pytest.mark.parametrize('field', ['notify_email', 'smtp_username', 'smtp_from', 'telegram_chat_id', 'telegram_bot_token', 'brand_api_key'])
def test_plain_field_explicit_empty_clears_only_that_field(client, login, csrf_post, field):
    login()
    db.update_settings(**STORED)
    csrf_post('/settings', data={field: '  '})
    actual = dict(db.get_settings())
    for key, value in STORED.items():
        assert actual[key] == ('' if key == field else value)


@pytest.mark.parametrize('port', ['', 'garbage', '0', '-1', '65536'])
def test_email_empty_host_and_invalid_port_use_defaults(client, login, csrf_post, port):
    login()
    db.update_settings(**STORED)
    response = csrf_post('/settings', data={
        'section': 'email', 'smtp_host': '  ', 'smtp_port': port,
    }, follow_redirects=True)
    assert response.status_code == 200
    assert db.get_settings()['smtp_host'] == 'localhost'
    assert db.get_settings()['smtp_port'] == 25
    assert 'name="smtp_host" placeholder="localhost" value="localhost"' in response.text


@pytest.mark.parametrize('host', ['', 'smtp.example.com'])
def test_settings_renders_stored_smtp_host(client, login, host):
    login()
    db.update_settings(smtp_host=host)
    assert f'name="smtp_host" placeholder="localhost" value="{host}"' in client.get('/settings').text


def test_telegram_save_preserves_empty_smtp_settings(client, login, csrf_post):
    login()
    db.update_settings(smtp_host='', smtp_port=2525)
    response = csrf_post('/settings', data={'section': 'telegram', 'telegram_chat_id': '43'})
    assert response.status_code == 302
    assert db.get_settings()['smtp_host'] == ''
    assert db.get_settings()['smtp_port'] == 2525


def test_settings_independent_forms_and_status(client, login):
    login()
    html = client.get('/settings').text
    sections = re.findall(r'<section class="panel settings-section".*?</section>', html, re.S)
    assert len(sections) == 4
    assert html.count('state-marker--on') == 0
    expected = [('email', 'notify_email', 'settings-email-title'), ('telegram', 'telegram_bot_token', 'settings-telegram-title'), ('brand', 'brand_api_key', 'settings-brand-title')]
    for section, (name, field, title) in zip(sections, expected):
        assert section.count('<form ') == 1 and section.count('</form>') == 1
        assert f'name="section" value="{name}"' in section
        assert f'name="{field}"' in section
        assert f'id="{title}"' in section
        assert 'name="csrf_token"' in section
        for other in {'notify_email', 'telegram_bot_token', 'brand_api_key'} - {field}:
            assert f'name="{other}"' not in section
    assert 'Сохранить и отправить тестовое письмо' in sections[0]
    assert 'Сохранить и отправить тест в Telegram' in sections[1]
    db.update_settings(**STORED)
    db.generate_api_key()
    assert client.get('/settings').text.count('state-marker--on') == 4
    db.update_settings(telegram_chat_id='')
    assert client.get('/settings').text.count('state-marker--on') == 3


@pytest.mark.parametrize('section,endpoint', [('email', '/settings/test-email'), ('telegram', '/settings/test-telegram')])
def test_test_send_button_submits_its_own_section_form(client, login, section, endpoint):
    # Кнопка тестовой отправки должна лежать внутри формы своей секции:
    # иначе браузер отправит чужие поля (или никакие) и раздельное
    # сохранение сломается незаметно для остальных тестов.
    login()
    html = client.get('/settings').text
    forms = re.findall(r'<form method="post" action="/settings".*?</form>', html, re.S)
    own = [f for f in forms if f'name="section" value="{section}"' in f]
    assert len(own) == 1
    assert f'formaction="{endpoint}"' in own[0]
    others = [f for f in forms if f is not own[0]]
    assert all(f'formaction="{endpoint}"' not in f for f in others)
