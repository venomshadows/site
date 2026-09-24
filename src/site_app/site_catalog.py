"""Каталог собственных доменов для внешнего API: все домены из domains и drops,
без учёта бренда, поисковика, статуса и привязки — чтобы на них не подавались жалобы."""
from site_app.db import _connect

_ALL_DOMAINS_SQL = 'SELECT domain FROM domains UNION SELECT domain FROM drops ORDER BY domain'


def all_domains() -> list[str]:
    with _connect() as conn:
        return [row['domain'] for row in conn.execute(_ALL_DOMAINS_SQL)]
