"""Настройки уведомлений и API-ключей."""
from flask import Blueprint, redirect, render_template, request, url_for
from site_app import db, notifications
from site_app.auth import login_required

settings_bp = Blueprint("settings", __name__)
SECRET_FIELDS = ("smtp_password",)
VISIBLE_KEY_FIELDS = ("telegram_bot_token", "brand_api_key")


@settings_bp.get("/settings")
@login_required
def settings_page():
    return _render_settings(saved=request.args.get("saved") is not None)


@settings_bp.post("/settings")
@login_required
def settings_save():
    _save_settings(request.form)
    return redirect(url_for("settings.settings_page", saved=1))


@settings_bp.post("/settings/test-email")
@login_required
def settings_test_email():
    _save_settings(request.form)
    result = notifications.send_email(db.get_settings(), "Тестовое письмо — Site", "Уведомления сайта работают.")
    return _render_settings(test_result=_test_result_context("Email", result))


@settings_bp.post("/settings/test-telegram")
@login_required
def settings_test_telegram():
    _save_settings(request.form)
    result = notifications.send_telegram(db.get_settings(), "Тестовое сообщение — Site. Уведомления сайта работают.")
    return _render_settings(test_result=_test_result_context("Telegram", result))


@settings_bp.post("/settings/api-key/generate")
@login_required
def settings_api_key_generate():
    db.generate_api_key()
    return redirect(url_for("settings.settings_page"))


@settings_bp.post("/settings/api-key/revoke")
@login_required
def settings_api_key_revoke():
    db.revoke_api_key()
    return redirect(url_for("settings.settings_page"))


def _render_settings(*, saved: bool = False, test_result: dict | None = None):
    """settings.html всегда рендерится с одним и тем же набором данных
    (settings) плюс результат конкретного действия — вынесено сюда, чтобы
    не забыть какое-то поле в одном из нескольких POST-обработчиков
    /settings/*."""
    return render_template("settings.html", settings=db.get_settings(), saved=saved, test_result=test_result)


def _test_result_context(channel_label: str, result: notifications.SendResult) -> dict:
    return {
        "channel_label": channel_label,
        "ok": result.ok,
        "message": "Отправлено успешно — проверь получателя." if result.ok else result.error,
    }


def _save_settings(form) -> None:
    """Сохранить поля с /settings.

    Видимые telegram_bot_token и brand_api_key показывают текущее значение:
    явно пустое поле очищает ключ, отсутствующее в POST — сохраняет его.
    Единственный секрет smtp_password рендерится пустым: пусто = не менять,
    очистка возможна только через чекбокс __clear.
    """
    smtp_host = form.get("smtp_host", "").strip() or "localhost"
    try:
        smtp_port = int(form.get("smtp_port", "").strip() or "25")
    except ValueError:
        smtp_port = 25
    # SQLite INTEGER не резиновый: значение вне диапазона портов (в том
    # числе абсурдно большое число, введённое в поле типа number руками
    # или через прямой POST мимо валидации браузера) роняло бы сохранение
    # OverflowError'ом. Не в диапазоне — считаем как нечисловой ввод.
    if not 1 <= smtp_port <= 65535:
        smtp_port = 25

    fields: dict[str, object] = {
        "notify_email": form.get("notify_email", "").strip(),
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_username": form.get("smtp_username", "").strip(),
        "smtp_use_tls": 1 if form.get("smtp_use_tls") else 0,
        "smtp_from": form.get("smtp_from", "").strip(),
        "telegram_chat_id": form.get("telegram_chat_id", "").strip(),
    }
    fields.update({name: form[name].strip() for name in VISIBLE_KEY_FIELDS if name in form})
    fields.update(_secret_field_updates(form))
    db.update_settings(**fields)


def _secret_field_updates(form) -> dict:
    """Обновить smtp_password: пусто = не менять, __clear = очистить.

    Пробелы могут быть частью сгенерированного пароля, поэтому сохраняем
    его без обрезки. Проверка strip() только исключает пустой ввод.
    """
    fields = {}
    for name in SECRET_FIELDS:
        value = form.get(name, "")
        # Явная очистка имеет приоритет даже при одновременно введённом новом
        # значении: отмеченный чекбокс всегда удаляет секрет из базы.
        if form.get(f"{name}__clear") == "1":
            fields[name] = None
        elif value.strip():
            fields[name] = value
    return fields


