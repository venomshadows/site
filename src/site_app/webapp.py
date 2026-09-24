"""Фабрика приложения и маршруты сайта."""
import datetime as dt
import os
from zoneinfo import ZoneInfo
from flask import Blueprint, Flask, Response, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from site_app.auth import login_required
from site_app.auth_views import auth_bp
from site_app import db, brand_client
from site_app.brands_views import brands_bp, render_brand_page, DEFAULT_TAB
from site_app.settings_views import settings_bp
from site_app.api_views import api_bp

_REQUIRED_ENV = ("SECRET_KEY", "AUTH_USERNAME", "AUTH_PASSWORD_HASH", "AUTH_SECOND_PASSWORD_HASH")
_DISPLAY_TZ = ZoneInfo("Europe/Moscow")
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


def load_config() -> dict:
    return {
        **{name: os.environ.get(name) for name in _REQUIRED_ENV},
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": os.environ.get("SESSION_COOKIE_SECURE", "1") != "0",
        "PERMANENT_SESSION_LIFETIME": 12 * 60 * 60,
    }


pages = Blueprint("pages", __name__)


@pages.get("/")
@login_required
def index() -> str | Response:
    brands, brands_error = brand_client.list_brands()
    if brands:
        return redirect(url_for("brands.brand_tab", brand_id=brands[0]["id"], tab=DEFAULT_TAB))
    return render_brand_page(
        "index.html", brands=brands, brands_error=brands_error,
        brand=None, brand_id=None, active_tab=None,
    )


@pages.get("/healthz")
def healthz() -> Response:
    return Response("ok", mimetype="text/plain")


@pages.get("/robots.txt")
def robots() -> Response:
    return Response(ROBOTS_TXT, mimetype="text/plain")


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(load_config())
    if config is not None:
        app.config.from_mapping(config)
    missing = [name for name in _REQUIRED_ENV if not app.config.get(name)]
    if missing:
        raise RuntimeError(
            "Не заданы переменные окружения: " + ", ".join(missing)
            + ". Скопируй .env.example в .env, заполни его (пароли — через "
            "deploy/gen_password_hash.py) и перезапусти сервис."
        )

    # Доверяем адресу и схеме только одного прокси — nginx.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    @app.template_filter("fmt_datetime")
    def fmt_datetime(value: str | None) -> str:
        if not value:
            return "—"
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(_DISPLAY_TZ)
        return parsed.strftime("%d.%m.%Y %H:%M")

    @app.template_global()
    def static_url(filename: str) -> str:
        """Версия файла в наносекундах сбрасывает кэш после обновления."""
        try:
            version = os.stat(os.path.join(app.static_folder, filename)).st_mtime_ns
        except OSError:
            version = 0
        return url_for("static", filename=filename, v=version)

    db.init_db()
    app.register_blueprint(brands_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages)
    return app
