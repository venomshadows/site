"""Согласованность приложения и деплой-артефактов (deploy/, .env.example,
pyproject.toml).

pytest ходит в приложение через WSGI и не видит юнит, nginx и update.sh —
без этих тестов переименованный эндпоинт или сменённый в одном месте порт
прошли бы CI и сломали деплой уже на сервере.
"""

import fnmatch
import re
import tomllib
from pathlib import Path

import pytest

from site_app.webapp import _REQUIRED_ENV

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
PACKAGE = ROOT / "src" / "site_app"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def single(pattern: str, text: str) -> str:
    """Единственное совпадение группы: ноль или несколько — уже рассинхрон."""
    found = set(re.findall(pattern, text, flags=re.M))
    assert len(found) == 1, f"{pattern!r}: ожидалось одно значение, найдено {found}"
    return found.pop()


def test_upstream_address_matches_everywhere():
    bind = single(r"--bind (\S+)", read("deploy/site.service"))
    upstream = single(r"proxy_pass http://([^;/]+)", read("deploy/nginx.conf.example"))
    probes = set(re.findall(r"http://([^/\"]+)/", read("deploy/update.sh")))
    assert {bind, upstream} | probes == {bind}


def test_update_probes_existing_routes(app):
    probed = set(re.findall(r"http://[^/\"]+(/[^\"]*)\"", read("deploy/update.sh")))
    routes = {rule.rule for rule in app.url_map.iter_rules()}
    assert probed and probed <= routes


def test_service_name_matches_unit_file():
    service = single(r'^SERVICE="([^"]+)"', read("deploy/update.sh"))
    assert (DEPLOY / f"{service}.service").is_file()


def test_nginx_static_alias_points_to_package_static(app):
    alias = single(r"alias (\S+);", read("deploy/nginx.conf.example"))
    app_dir = single(r"^WorkingDirectory=(\S+)", read("deploy/site.service"))
    expected = Path(app.static_folder).relative_to(ROOT).as_posix()
    assert alias.rstrip("/") == f"{app_dir}/{expected}"


def test_env_example_lists_exactly_known_variables():
    names = set(re.findall(r"^([A-Z_]+)=", read(".env.example"), flags=re.M))
    assert names == {*_REQUIRED_ENV, "SESSION_COOKIE_SECURE"}


def static_references() -> set[str]:
    refs = set()
    for template in (PACKAGE / "templates").glob("*.html"):
        refs |= set(re.findall(r"static_url\('([^']+)'\)", template.read_text(encoding="utf-8")))
    return refs


@pytest.mark.parametrize("filename", sorted(static_references()))
def test_static_reference_exists_and_is_packaged(filename):
    assert (PACKAGE / "static" / filename).is_file()
    globs = tomllib.loads(read("pyproject.toml"))["tool"]["setuptools"]["package-data"]["site_app"]
    assert any(fnmatch.fnmatch(f"static/{filename}", g) for g in globs), f"{filename} не попадёт в wheel"
