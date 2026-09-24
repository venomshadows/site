"""
Отправка уведомлений (email + Telegram) — настраиваются на /settings,
проверяются оттуда же кнопками "Отправить тестовое…".

Email отправляется через SMTP, настроенный на /settings — по умолчанию
localhost:25 без авторизации (сервер должен сам быть настроен как relay,
см. README), либо внешний SMTP с логином/паролем и STARTTLS.

Telegram — обычный Bot API (https://api.telegram.org/bot<token>/...),
получателем должен быть chat_id, с которым бот уже "знаком" (получателю
нужно один раз самому написать боту /start — Bot API не даёт писать
первым тем, кто ещё не начал с ним диалог).

Транспорт для проверки каналов со страницы /settings.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import requests

logger = logging.getLogger(__name__)

_SMTP_TIMEOUT = 10
_TELEGRAM_TIMEOUT = 10

# Отправитель писем, если в /settings поле "От кого" пустое.
_DEFAULT_SMTP_FROM = "site@site.venomshadows.ru"


@dataclass
class SendResult:
    ok: bool
    error: str | None = None


def send_email(settings: object, subject: str, body: str, html_body: str | None = None) -> SendResult:
    """settings — строка из db.get_settings() (sqlite3.Row) либо любой
    объект с тем же доступом по ключу (settings[...])."""
    to_addr = settings["notify_email"]
    if not to_addr:
        return SendResult(ok=False, error="Email для уведомлений не задан в настройках")

    try:
        msg = EmailMessage()
        # Присвоение заголовка бросает ValueError, если значение содержит
        # \r или \n (EmailMessage сама так защищается от header injection —
        # дописывания Bcc/лишних заголовков через перевод строки в поле
        # "Email для уведомлений" или "Адрес отправителя") — ловим её здесь
        # же, вместе с сетевыми ошибками, а не даём 500-й уронить /settings.
        msg["Subject"] = subject
        msg["From"] = settings["smtp_from"] or _DEFAULT_SMTP_FROM
        msg["To"] = to_addr
        msg.set_content(body)
        if html_body is not None:
            msg.add_alternative(html_body, subtype='html')

        with smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=_SMTP_TIMEOUT) as smtp:
            if settings["smtp_use_tls"]:
                # Без явного context smtplib берёт контекст с CERT_NONE
                # (сертификат сервера не проверяется вообще) — STARTTLS
                # тогда защищает только от пассивного прослушивания, но не
                # от MITM с подменой сертификата, через который улетел бы
                # пароль SMTP. create_default_context() включает проверку
                # цепочки сертификата и имени хоста, как в браузере.
                smtp.starttls(context=ssl.create_default_context())
            if settings["smtp_username"]:
                smtp.login(settings["smtp_username"], settings["smtp_password"] or "")
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        logger.warning("Не удалось отправить email на %s: %s", to_addr, exc)
        return SendResult(ok=False, error=str(exc))
    return SendResult(ok=True)


def _redact_token(text: str, token: str) -> str:
    # requests включает URL с токеном в ошибки соединения; Bot API может повторить
    # токен и вне URL. Маскируем все вхождения до записи в лог и показа пользователю.
    return text.replace(token, "<redacted>") if token else text


def send_telegram(settings: object, text: str) -> SendResult:
    token = settings["telegram_bot_token"]
    chat_id = settings["telegram_chat_id"]
    if not token or not chat_id:
        return SendResult(ok=False, error="Telegram-бот не настроен (нужны токен и chat_id)")

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=_TELEGRAM_TIMEOUT,
        )
        # Не response.raise_for_status() до json(): при ошибке Bot API само
        # тело ответа несёт description с понятной причиной отказа.
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        error = _redact_token(str(exc), token)
        logger.warning("Не удалось отправить сообщение в Telegram: %s", error)
        return SendResult(ok=False, error=error)

    # Валидный JSON не обязательно является объектом: null или массив тоже
    # успешно разбираются. Проверяем тип до обращения к .get(), чтобы сбой
    # Bot API вернулся понятной ошибкой, а не уронил страницу настроек.
    if not isinstance(payload, dict):
        error = _redact_token("Telegram Bot API вернул неожиданный ответ", token)
        logger.warning("Не удалось отправить сообщение в Telegram: %s", error)
        return SendResult(ok=False, error=error)

    if payload.get("ok"):
        return SendResult(ok=True)

    error = payload.get("description") or "неизвестная ошибка Telegram API"
    error = _redact_token(error, token)
    logger.warning("Telegram API отклонил сообщение: %s", error)
    return SendResult(ok=False, error=error)
