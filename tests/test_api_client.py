from unittest.mock import Mock, patch

import pytest
import requests

from site_app import api_client, db


def fetch():
    return api_client.get(service_name='Brand', api_key_field='brand_api_key',
                          base_url='https://brand.example', path='/api/v1/brands')


def test_missing_key_does_not_request(app):
    with patch.object(requests, 'get') as request:
        result = fetch()
    assert not result.ok and 'не задан' in result.error
    request.assert_not_called()


def test_network_error_does_not_expose_secret(app):
    db.update_settings(brand_api_key='secret')
    with patch.object(requests, 'get', side_effect=requests.RequestException('secret')):
        result = fetch()
    assert not result.ok and 'недоступен' in result.error
    assert 'secret' not in result.error


@pytest.mark.parametrize('status, expected', [(401, 'API-ключ'), (403, '403'), (503, '503')])
def test_http_errors(app, status, expected):
    db.update_settings(brand_api_key='secret')
    with patch.object(requests, 'get', return_value=Mock(status_code=status, ok=False)):
        result = fetch()
    assert not result.ok and expected in result.error


def test_invalid_json(app):
    db.update_settings(brand_api_key='secret')
    response = Mock(status_code=200, ok=True)
    response.json.side_effect = ValueError()
    with patch.object(requests, 'get', return_value=response):
        result = fetch()
    assert not result.ok and 'JSON' in result.error


@pytest.mark.parametrize('payload', [None, [], 'text'])
def test_non_object_json(app, payload):
    db.update_settings(brand_api_key='secret')
    response = Mock(status_code=200, ok=True)
    response.json.return_value = payload
    with patch.object(requests, 'get', return_value=response):
        result = fetch()
    assert not result.ok and 'не JSON-объект' in result.error


def test_success_uses_settings_key_and_timeout(app):
    db.update_settings(brand_api_key='secret')
    response = Mock(status_code=200, ok=True)
    response.json.return_value = {'brands': []}
    with patch.object(requests, 'get', return_value=response) as request:
        result = fetch()
    assert result.ok and result.data == {'brands': []}
    request.assert_called_once_with('https://brand.example/api/v1/brands',
                                    headers={'Authorization': 'Bearer secret'}, params=None, timeout=8)


def test_extract_missing_list():
    assert api_client.extract_list({}, 'brands', service_name='Brand') == ([], None)


@pytest.mark.parametrize('value', [None, 'text', {}, [1]])
def test_extract_malformed_list(value):
    records, error = api_client.extract_list({'brands': value}, 'brands', service_name='Brand')
    assert records == [] and 'неожиданном формате' in error
