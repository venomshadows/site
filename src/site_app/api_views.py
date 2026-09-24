from flask import Blueprint, jsonify
from site_app.auth import api_key_required

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


@api_bp.get("/ping")
@api_key_required
def ping():
    return jsonify(ok=True)
