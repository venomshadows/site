"""
Тесты отправки уведомлений (site_app.notifications): email через
SMTP и Telegram Bot API — оба замоканы (smtplib.SMTP / requests.post),
реальная сеть недоступна из песочницы.
"""

import smtplib
import ssl
import unittest
from unittest.mock import MagicMock, patch

from site_app import db, notifications
import pytest


@pytest.fixture(autouse=True)
def database(clean_state):
    db.init_db()


class TestSendEmail(unittest.TestCase):
    def test_html_email_retains_plain_text_alternative(self):
        db.update_settings(notify_email='test@example.invalid')
        with patch.object(smtplib, 'SMTP') as smtp:
            result = notifications.send_email(db.get_settings(), 'Тема', 'Текст', html_body='<p>Текст</p>')
        self.assertTrue(result.ok)
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(message.get_content_type(), 'multipart/alternative')
        self.assertEqual(message.get_body(preferencelist=('plain',)).get_content().strip(), 'Текст')
        self.assertEqual(message.get_body(preferencelist=('html',)).get_content().strip(), '<p>Текст</p>')

    def test_no_recipient_configured_fails_without_touching_smtp(self):
        settings = db.get_settings()
        with patch.object(smtplib, "SMTP") as mock_smtp:
            result = notifications.send_email(settings, "Тема", "Текст")
        self.assertFalse(result.ok)
        self.assertIn("Email", result.error)
        mock_smtp.assert_not_called()

    def test_successful_send_uses_configured_host_and_port(self):
        db.update_settings(notify_email="admin@example.com", smtp_host="127.0.0.1", smtp_port=2525)
        settings = db.get_settings()

        mock_conn = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_conn

        with patch.object(smtplib, "SMTP", mock_smtp_cls):
            result = notifications.send_email(settings, "Тема", "Текст письма")

        self.assertTrue(result.ok)
        mock_smtp_cls.assert_called_once_with("127.0.0.1", 2525, timeout=notifications._SMTP_TIMEOUT)
        mock_conn.send_message.assert_called_once()
        sent_msg = mock_conn.send_message.call_args[0][0]
        self.assertEqual(sent_msg["To"], "admin@example.com")
        self.assertEqual(sent_msg["Subject"], "Тема")
        self.assertEqual(sent_msg["From"], notifications._DEFAULT_SMTP_FROM)
        mock_conn.starttls.assert_not_called()  # smtp_use_tls по умолчанию 0
        mock_conn.login.assert_not_called()  # smtp_username не задан

    def test_custom_from_address_is_used_when_set(self):
        db.update_settings(notify_email="admin@example.com", smtp_from="alerts@example.com")
        settings = db.get_settings()

        mock_conn = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_conn

        with patch.object(smtplib, "SMTP", mock_smtp_cls):
            notifications.send_email(settings, "Тема", "Текст")

        sent_msg = mock_conn.send_message.call_args[0][0]
        self.assertEqual(sent_msg["From"], "alerts@example.com")

    def test_use_tls_and_login_called_when_configured(self):
        db.update_settings(
            notify_email="admin@example.com",
            smtp_use_tls=1,
            smtp_username="bot",
            smtp_password="secret",
        )
        settings = db.get_settings()

        mock_conn = MagicMock()
        mock_smtp_cls = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_conn

        with patch.object(smtplib, "SMTP", mock_smtp_cls):
            result = notifications.send_email(settings, "Тема", "Текст")

        self.assertTrue(result.ok)
        mock_conn.starttls.assert_called_once()
        # Без явного SSL-контекста smtplib не проверяет сертификат сервера
        # (context=None -> verify_mode=CERT_NONE) — STARTTLS тогда защищает
        # только от пассивного прослушивания, но не от MITM с подменой
        # сертификата, через который улетел бы пароль SMTP.
        starttls_context = mock_conn.starttls.call_args.kwargs.get("context")
        self.assertIsInstance(starttls_context, ssl.SSLContext)
        self.assertEqual(starttls_context.verify_mode, ssl.CERT_REQUIRED)
        mock_conn.login.assert_called_once_with("bot", "secret")

    def test_smtp_failure_is_reported_not_raised(self):
        db.update_settings(notify_email="admin@example.com")
        settings = db.get_settings()

        with patch.object(smtplib, "SMTP", side_effect=OSError("connection refused")):
            result = notifications.send_email(settings, "Тема", "Текст")

        self.assertFalse(result.ok)
        self.assertIn("connection refused", result.error)

    def test_header_injection_in_recipient_is_reported_not_raised(self):
        """EmailMessage сама бросает ValueError на перевод строки в значении
        заголовка (защита от дописывания Bcc и т.п. через notify_email) —
        send_email обязана превратить это в SendResult, а не уронить вызывающего
        код 500-й ошибкой."""
        db.update_settings(notify_email="a@example.com\r\nBcc: attacker@evil.com")
        settings = db.get_settings()

        with patch.object(smtplib, "SMTP") as mock_smtp:
            result = notifications.send_email(settings, "Тема", "Текст")

        self.assertFalse(result.ok)
        mock_smtp.assert_not_called()


class TestSendTelegram(unittest.TestCase):
    def test_token_is_redacted_in_network_errors_and_api_rejections(self):
        token = '123456:ABC-DEF'
        db.update_settings(telegram_bot_token=token, telegram_chat_id='42')
        error = f'HTTPSConnectionPool(api.telegram.org): url: /bot{token}/sendMessage token={token}'
        response = MagicMock()
        response.json.return_value = {'ok': False, 'description': error}
        for kwargs in (
            {'side_effect': notifications.requests.ConnectionError(error)},
            {'return_value': response},
        ):
            with self.subTest(kwargs=kwargs):
                with patch('site_app.notifications.requests.post', **kwargs), self.assertLogs(
                    notifications.logger, level='WARNING'
                ) as logs:
                    result = notifications.send_telegram(db.get_settings(), 'привет')
                self.assertFalse(result.ok)
                self.assertNotIn(token, result.error)
                self.assertIn('/bot<redacted>/sendMessage token=<redacted>', result.error)
                self.assertNotIn(token, '\n'.join(logs.output))

    def test_missing_token_or_chat_id_fails_without_request(self):
        settings = db.get_settings()
        with patch("site_app.notifications.requests.post") as mock_post:
            result = notifications.send_telegram(settings, "привет")
        self.assertFalse(result.ok)
        self.assertIn("Telegram", result.error)
        mock_post.assert_not_called()

    def test_successful_send(self):
        db.update_settings(telegram_bot_token="123:ABC", telegram_chat_id="42")
        settings = db.get_settings()

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        with patch("site_app.notifications.requests.post", return_value=mock_response) as mock_post:
            result = notifications.send_telegram(settings, "привет")

        self.assertTrue(result.ok)
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        self.assertIn("123:ABC", url)
        self.assertEqual(mock_post.call_args[1]["data"], {"chat_id": "42", "text": "привет"})

    def test_non_object_json_is_reported_not_raised(self):
        db.update_settings(telegram_bot_token="123:ABC", telegram_chat_id="42")
        settings = db.get_settings()

        for payload in (None, ["123:ABC"]):
            with self.subTest(payload=payload):
                mock_response = MagicMock()
                mock_response.json.return_value = payload

                with patch("site_app.notifications.requests.post", return_value=mock_response), self.assertLogs(
                    notifications.logger, level="WARNING"
                ) as logs:
                    result = notifications.send_telegram(settings, "привет")

                self.assertIsInstance(result, notifications.SendResult)
                self.assertFalse(result.ok)
                self.assertIn("неожиданный ответ", result.error)
                self.assertNotIn("123:ABC", result.error)
                self.assertNotIn("123:ABC", "\n".join(logs.output))

    def test_api_rejection_is_reported(self):
        db.update_settings(telegram_bot_token="123:ABC", telegram_chat_id="42")
        settings = db.get_settings()

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "description": "chat not found"}

        with patch("site_app.notifications.requests.post", return_value=mock_response):
            result = notifications.send_telegram(settings, "привет")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "chat not found")

    def test_network_failure_is_reported_not_raised(self):
        import requests

        db.update_settings(telegram_bot_token="123:ABC", telegram_chat_id="42")
        settings = db.get_settings()

        with patch(
            "site_app.notifications.requests.post",
            side_effect=requests.RequestException("timeout"),
        ):
            result = notifications.send_telegram(settings, "привет")

        self.assertFalse(result.ok)
        self.assertIn("timeout", result.error)


if __name__ == "__main__":
    unittest.main()
