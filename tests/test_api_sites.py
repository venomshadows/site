import pytest

from site_app import db

NOW = '2026-01-01T00:00:00+00:00'


def auth_headers(key, header):
    return {'Authorization': f'Bearer {key}'} if header == 'Authorization' else {'X-API-Key': key}


def fetch(client, key, header='X-API-Key'):
    return client.get('/api/v1/sites', headers=auth_headers(key, header))


def add_domain(domain, brand_id=1, engine='yandex', status='new', parent_id=None):
    with db._connect() as conn:
        return conn.execute(
            'INSERT INTO domains (brand_id, engine, domain, status, parent_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (brand_id, engine, domain, status, parent_id, NOW)).lastrowid


def add_drop(domain):
    # Только общие поля drops: схема на сервере может быть старее локальной.
    with db._connect() as conn:
        conn.execute('INSERT INTO drops (domain, created_at) VALUES (?, ?)', (domain, NOW))


def domains_of(response):
    return [item['domain'] for item in response.json['sites']]


@pytest.mark.parametrize('header', ['Authorization', 'X-API-Key'])
def test_empty_list(app, client, header):
    response = fetch(client, db.generate_api_key(), header)
    assert response.status_code == 200
    assert response.json == {'sites': []}


def test_missing_key_rejected(app, client):
    db.generate_api_key()
    assert client.get('/api/v1/sites').status_code == 401


@pytest.mark.parametrize('header', ['Authorization', 'X-API-Key'])
def test_wrong_key_rejected(app, client, header):
    db.generate_api_key()
    add_domain('own.example')
    response = fetch(client, 'wrong-key', header)
    assert response.status_code == 401
    assert 'own.example' not in response.get_data(as_text=True)


def test_no_key_configured_rejected(app, client):
    assert fetch(client, 'anything').status_code == 401


def test_ui_session_does_not_replace_api_key(app, client, login):
    login()
    db.generate_api_key()
    assert client.get('/api/v1/sites').status_code == 401
    assert fetch(client, 'wrong-key').status_code == 401


def test_union_and_dedup_across_tables_brands_engines_statuses(app, client):
    key = db.generate_api_key()
    parent = add_domain('b.example', brand_id=1, engine='yandex', status='new')
    add_domain('child.b.example', brand_id=1, engine='yandex', status='banned', parent_id=parent)
    add_domain('b.example', brand_id=2, engine='google', status='active')
    add_domain('shared.example', brand_id=3, engine='google')
    add_drop('shared.example')
    add_drop('a-drop.example')
    add_drop('drop-only.example')
    response = fetch(client, key)
    assert response.status_code == 200
    assert domains_of(response) == sorted(
        ['a-drop.example', 'b.example', 'child.b.example', 'drop-only.example', 'shared.example'])


def test_only_domain_field_exposed(app, client):
    key = db.generate_api_key()
    db.update_settings(brand_api_key='brand-secret', smtp_password='smtp-secret')
    add_domain('own.example', brand_id=42, status='banned')
    add_drop('drop.example')
    response = fetch(client, key)
    assert response.json == {'sites': [
        {'domain': 'drop.example', 'domain_unicode': 'drop.example'},
        {'domain': 'own.example', 'domain_unicode': 'own.example'},
    ]}
    body = response.get_data(as_text=True)
    for secret in (key, 'brand-secret', 'smtp-secret', 'banned', 'yandex'):
        assert secret not in body


def test_idn_domain_has_unicode_variant(app, client):
    key = db.generate_api_key()
    add_drop('xn--e1afmkfd.xn--p1ai')
    response = fetch(client, key)
    assert response.json == {'sites': [{'domain': 'xn--e1afmkfd.xn--p1ai', 'domain_unicode': 'пример.рф'}]}


def test_broken_punycode_does_not_break_endpoint(app, client):
    key = db.generate_api_key()
    add_drop('xn--zz')
    response = fetch(client, key)
    assert response.status_code == 200
    assert response.json == {'sites': [{'domain': 'xn--zz', 'domain_unicode': 'xn--zz'}]}
