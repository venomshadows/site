import os
import re
import sqlite3
from html.parser import HTMLParser
from unittest.mock import MagicMock

import pytest

from site_app import db, notifications


def test_schema_and_migration():
    with sqlite3.connect(os.environ['DATABASE_PATH']) as conn:
        conn.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1), notify_email TEXT)')
        conn.execute("INSERT INTO settings VALUES (1, 'keep@example.com')")
    db.init_db()
    db.init_db()
    row = db.get_settings()
    assert set(row.keys()) == {'id', *db.SETTINGS_COLUMNS}
    assert row['notify_email'] == 'keep@example.com'
    assert row['smtp_host'] == 'localhost'
    assert row['smtp_port'] == 25
    assert row['smtp_use_tls'] == 0


def test_whitelist_and_missing_row(app):
    with pytest.raises(ValueError):
        db.update_settings(notify_email='bad', **{'id = 2': 1})
    assert db.get_settings()['notify_email'] is None
    db.update_settings()
    with db._connect() as conn:
        conn.execute('DELETE FROM settings')
    db.update_settings(notify_email='restored@example.com')
    assert db.get_settings()['notify_email'] == 'restored@example.com'
    with db._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO settings (id) VALUES (2)')


def test_default_path(monkeypatch, tmp_path):
    monkeypatch.delenv('DATABASE_PATH')
    monkeypatch.chdir(tmp_path)
    db.init_db()
    assert (tmp_path / 'data/site.db').is_file()


ROUTES = [('get', '/settings')] + [('post', path) for path in (
    '/settings', '/settings/test-email', '/settings/test-telegram',
    '/settings/api-key/generate', '/settings/api-key/revoke',
)]


@pytest.mark.parametrize('method,path', ROUTES)
@pytest.mark.parametrize('stage', [None, 1, 2])
def test_settings_auth_and_no_store(client, method, path, stage, csrf_post):
    if stage:
        with client.session_transaction() as session:
            session['stage'] = stage
    response = (csrf_post(path) if method == "post" else client.get(path))
    assert response.headers['Cache-Control'] == 'no-store'
    if stage != 2:
        assert response.status_code == 302
        assert response.location.endswith('/login')
        assert db.get_settings()['api_key'] is None
    elif method == 'get':
        assert response.status_code == 200
        assert 'Настройки' in response.text
    elif path in ('/settings/test-email', '/settings/test-telegram'):
        # Тестовые отправки сразу показывают результат на странице настроек,
        # поэтому здесь ожидается 200. Редирект на вход означал бы отказ
        # авторизации даже при полностью завершённой сессии.
        assert response.status_code == 200
        assert 'Настройки' in response.text
    else:
        assert response.status_code == 302
        assert not response.location.endswith('/login')


@pytest.mark.parametrize('field', ['brand_api_key', 'telegram_bot_token'])
@pytest.mark.parametrize('blank', ['', '   '])
def test_visible_keys_save_and_clear(client, login, field, blank, csrf_post):
    login()
    value = 'distinctive-visible-key'
    response = csrf_post('/settings', data={field: f'  {value}  '}, follow_redirects=True)
    assert response.status_code == 200
    assert db.get_settings()[field] == value
    for html in (response.text, client.get('/settings').text):
        input_tag = re.search(rf'<input\b[^>]*name="{field}"[^>]*>', html).group(0)
        assert 'type="text"' in input_tag
        assert 'autocomplete="off"' in input_tag
        assert f'value="{value}"' in input_tag
        assert f'name="{field}__clear"' not in html
    response = csrf_post('/settings', data={field: blank}, follow_redirects=True)
    assert response.status_code == 200
    assert db.get_settings()[field] == ''
    assert value not in response.text
    if field == 'brand_api_key':
        input_tag = re.search(rf'<input\b[^>]*name="{field}"[^>]*>', response.text).group(0)
        assert 'placeholder="Ключ ещё не задан"' in input_tag


@pytest.mark.parametrize('field', ['brand_api_key', 'telegram_bot_token'])
@pytest.mark.parametrize('quote', ["'", '"'])
def test_visible_keys_escape_value(client, login, field, quote, csrf_post):
    login()
    value = f"x{quote} onload={quote}alert(1) & <b>"
    response = csrf_post('/settings', data={field: f'  {value}  '}, follow_redirects=True)
    assert response.status_code == 200
    assert db.get_settings()[field] == value

    class InputParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'input' and dict(attrs).get('name') == field:
                self.attrs = dict(attrs)

    for html in (response.text, client.get('/settings').text):
        input_tag = re.search(rf'<input\b[^>]*name="{field}"[^>]*>', html).group(0)
        escaped_quote = '&#39;' if quote == "'" else '&#34;'
        assert f'value="x{escaped_quote} onload={escaped_quote}alert(1) &amp; &lt;b&gt;"' in input_tag
        parser = InputParser()
        parser.feed(html)
        assert parser.attrs['value'] == value
        assert 'onload' not in parser.attrs


@pytest.mark.parametrize('path', ['/settings', '/settings/test-email', '/settings/test-telegram'])
def test_missing_visible_keys_preserve_values(client, login, monkeypatch, csrf_post, path):
    login()
    stored = {'brand_api_key': 'saved-brand-key', 'telegram_bot_token': 'saved-bot-token'}
    db.update_settings(**stored)
    for sender in ('send_email', 'send_telegram'):
        monkeypatch.setattr(notifications, sender, MagicMock(return_value=notifications.SendResult(ok=True)))
    response = csrf_post(path, data={'notify_email': 'updated@example.com'}, follow_redirects=True)
    assert response.status_code == 200
    settings = db.get_settings()
    assert settings['notify_email'] == 'updated@example.com'
    for field, value in stored.items():
        assert settings[field] == value
        assert f'value="{value}"' in response.text


@pytest.mark.parametrize('field', ['smtp_password'])
def test_secret_preservation(client, login, field, csrf_post):
    login()
    secret = '  distinctive-secret  '
    csrf_post('/settings', data={field: secret})
    for blank in ('', '   '):
        csrf_post('/settings', data={field: blank})
        assert db.get_settings()[field] == secret
    response = client.get('/settings')
    assert secret not in response.text
    assert 'Настройки' in response.text
    assert 'API-ключ сайта' in response.text


@pytest.mark.parametrize('field', ['smtp_password'])
@pytest.mark.parametrize('replacement', ['', 'new-secret'])
def test_secret_explicit_clear(client, login, field, replacement, csrf_post):
    login()
    assert f'name="{field}__clear"' not in client.get('/settings').text
    csrf_post('/settings', data={field: 'saved-secret'})
    assert f'name="{field}__clear" value="1"' in client.get('/settings').text
    response = csrf_post('/settings', data={field: replacement, f'{field}__clear': '1'}, follow_redirects=True)
    assert response.status_code == 200
    assert db.get_settings()[field] is None
    assert f'name="{field}__clear"' not in response.text


@pytest.mark.parametrize('value,expected', [
    ('2026-01-15T10:30:00+00:00', '15.01.2026 13:30'),
    ('2026-07-15T22:30:00+00:00', '16.07.2026 01:30'),
    ('2026-01-15T10:30:00', '15.01.2026 10:30'),
    (None, '—'), ('', '—'), ('invalid-date', 'invalid-date'),
])
def test_fmt_datetime(app, value, expected):
    assert app.jinja_env.filters['fmt_datetime'](value) == expected


def test_api_key_creation_date_in_moscow(client, login):
    login()
    db.generate_api_key()
    db.update_settings(api_key_created_at='2026-01-15T10:30:00+00:00')
    response = client.get('/settings')
    assert 'создан 15.01.2026 13:30)' in response.text
    assert '2026-01-15T10:30:00' not in response.text


@pytest.mark.parametrize('port,expected', [('', 25), ('abc', 25), ('0', 25), ('-1', 25),
    ('65536', 25), ('999999999999999999999999', 25), ('587', 587), ('65535', 65535)])
def test_port_validation(client, login, port, expected, csrf_post):
    login()
    response = csrf_post('/settings', data={'smtp_port': port}, follow_redirects=True)
    assert response.status_code == 200
    assert 'Настройки обновлены' in response.text
    assert db.get_settings()['smtp_port'] == expected


def test_key_lifecycle(client, login, csrf_post):
    assert not db.verify_api_key('anything')
    login()
    csrf_post('/settings/api-key/generate')
    key = db.get_settings()['api_key']
    assert key.startswith('site_') and len(key) > 40
    assert db.get_settings()['api_key_created_at']
    assert db.verify_api_key(key)
    assert not db.verify_api_key('ключ')
    assert key in client.get('/settings').text
    csrf_post('/settings/api-key/generate')
    assert not db.verify_api_key(key)
    new_key = db.get_settings()['api_key']
    assert new_key != key and db.verify_api_key(new_key)
    csrf_post('/settings/api-key/revoke')
    csrf_post('/settings/api-key/revoke')
    assert not db.verify_api_key(new_key)
    assert db.get_settings()['api_key_created_at'] is None


@pytest.mark.parametrize('header', ['Authorization', 'X-API-Key'])
def test_ping(client, header):
    assert client.get('/api/v1/ping').status_code == 401
    key = db.generate_api_key()
    for wrong in ('wrong', 'ключ'):
        value = f'Bearer {wrong}' if header == 'Authorization' else wrong
        response = client.get('/api/v1/ping', headers={header: value})
        assert response.status_code == 401 and response.is_json
    value = f'Bearer {key}' if header == 'Authorization' else key
    assert client.get('/api/v1/ping', headers={header: value}).json == {'ok': True}
    db.revoke_api_key()
    assert client.get('/api/v1/ping', headers={header: value}).status_code == 401


def test_email_form_saves_and_sends(client, login, monkeypatch, csrf_post):
    login()
    smtp = MagicMock()
    monkeypatch.setattr(notifications.smtplib, 'SMTP', smtp)
    response = csrf_post('/settings/test-email', data={
        'section': 'email',
        'notify_email': 'recipient@example.com', 'smtp_host': 'smtp.example.com',
        'smtp_port': '587', 'smtp_use_tls': '1', 'smtp_username': 'user', 'smtp_password': 'pass',
    })
    assert 'Отправлено успешно' in response.text
    smtp.assert_called_once_with('smtp.example.com', 587, timeout=10)
    smtp.return_value.__enter__.return_value.login.assert_called_once_with('user', 'pass')
    assert db.get_settings()['notify_email'] == 'recipient@example.com'
    smtp.return_value.__enter__.return_value.starttls.assert_called_once()
    smtp.side_effect = OSError('SMTP unavailable')
    response = csrf_post('/settings/test-email', data={'notify_email': 'a@example.com'})
    assert response.status_code == 200 and 'SMTP unavailable' in response.text


def test_telegram_form_saves_and_sends(client, login, monkeypatch, csrf_post):
    login()
    post = MagicMock()
    post.return_value.json.return_value = {'ok': True}
    monkeypatch.setattr(notifications.requests, 'post', post)
    form = {'telegram_bot_token': '123:ABC', 'telegram_chat_id': '42'}
    response = csrf_post('/settings/test-telegram', data=form)
    assert 'Отправлено успешно' in response.text
    assert db.get_settings()['telegram_chat_id'] == '42'
    assert post.call_args.args == ('https://api.telegram.org/bot123:ABC/sendMessage',)
    assert post.call_args.kwargs['data']['chat_id'] == '42'
    post.return_value.json.return_value = {'ok': False, 'description': '<b>denied</b>'}
    response = csrf_post('/settings/test-telegram', data=form)
    assert '&lt;b&gt;denied&lt;/b&gt;' in response.text


def test_telegram_failure_redacts_token_in_error(client, login, monkeypatch, csrf_post):
    login()
    token = '123456:ABC-DEF'
    csrf_post('/settings', data={'telegram_bot_token': token})
    post = MagicMock(side_effect=notifications.requests.ConnectionError(
        f'HTTPSConnectionPool(api.telegram.org): Max retries exceeded with url: /bot{token}/sendMessage'
    ))
    monkeypatch.setattr(notifications.requests, 'post', post)
    response = csrf_post('/settings/test-telegram', data={
        'telegram_bot_token': token, 'telegram_chat_id': '42',
    })
    assert response.status_code == 200
    error = re.search(r'<div class="result result--error">(.*?)</div>', response.text, re.S).group(1)
    assert token not in error
    assert '/bot&lt;redacted&gt;/sendMessage' in response.text
    assert db.get_settings()['telegram_bot_token'] == token
    assert f'value="{token}"' in response.text
    assert f'value="{token}"' in client.get('/settings').text
