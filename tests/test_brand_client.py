from unittest.mock import patch

import pytest

from site_app import api_client, brand_client


@pytest.mark.parametrize('value, expected', [
    ('https://brand.example/', 'https://brand.example'),
    ('', 'https://brand.venomshadows.ru'),
    (None, 'https://brand.venomshadows.ru'),
])
def test_api_base(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('BRAND_API_URL', raising=False)
    else:
        monkeypatch.setenv('BRAND_API_URL', value)
    assert brand_client._api_base() == expected


def test_brands_filter_invalid_records():
    good = {'id': 7, 'name': 'Бренд'}
    records = [{}, {'name': 'No id'}, {'id': '1', 'name': 'Bad id'},
               {'id': 2}, {'id': 3, 'name': ''}, good]
    with patch.object(api_client, 'get', return_value=api_client.ApiResult(True, {'brands': records})):
        assert brand_client.list_brands() == ([good], None)


def test_success_preserves_brands_and_order(monkeypatch):
    monkeypatch.delenv('BRAND_API_URL', raising=False)
    brands = [{'id': 9, 'name': 'Первый', 'query_frequency_total': 0},
              {'id': 2, 'name': 'Второй', 'query_frequency_total': None}]
    with patch.object(api_client, 'get', return_value=api_client.ApiResult(True, {'brands': brands})) as get:
        assert brand_client.list_brands() == (brands, None)
    get.assert_called_once_with(service_name='Brand', api_key_field='brand_api_key',
                                base_url='https://brand.venomshadows.ru', path='/api/v1/brands')


@pytest.mark.parametrize('result', [api_client.ApiResult(False, error='Brand недоступен'),
                                   api_client.ApiResult(True, {'brands': None})])
def test_errors_propagate(result):
    with patch.object(api_client, 'get', return_value=result):
        brands, error = brand_client.list_brands()
    assert brands == [] and error
