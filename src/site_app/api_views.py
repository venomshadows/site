from flask import Blueprint, jsonify
from site_app import domains, site_catalog
from site_app.auth import api_key_required

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


@api_bp.get("/ping")
@api_key_required
def ping():
    return jsonify(ok=True)


@api_bp.get("/sites")
@api_key_required
def sites():
    # domain — как хранится (IDN в punycode, контракт complaints-checker);
    # domain_unicode — для сравнения с хостами из выдачи, которые бывают
    # кириллицей. Для ASCII-доменов оба поля совпадают.
    return jsonify(sites=[
        {"domain": domain, "domain_unicode": domains.display_domain(domain)}
        for domain in site_catalog.all_domains()
    ])
