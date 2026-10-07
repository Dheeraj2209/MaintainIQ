"""Tests for src/notifications/email.py's SMTP transport selection.

dispatch-level fan-out is covered in test_notifications.py; this file covers
the layer below it: choosing plain SMTP (Mailpit) vs STARTTLS vs implicit
TLS, and whether to authenticate — i.e. what makes real-provider email
(Gmail/Outlook/SES/SendGrid) work rather than Mailpit only.
"""
import smtplib

import pytest

from src.notifications import email as email_mod


class _FakeSMTP:
    """Records the protocol conversation instead of talking to a server."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args = None
        self.sent = []
        self.closed = False
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, message):
        self.sent.append(message)


class _FakeSMTPSSL(_FakeSMTP):
    instances = []


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_USER",
        "SMTP_PASSWORD", "SMTP_STARTTLS", "SMTP_SSL", "SMTP_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    _FakeSMTP.instances = []
    _FakeSMTPSSL.instances = []


@pytest.fixture
def fake_smtp(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTPSSL)
    return _FakeSMTP


def test_defaults_to_plain_local_mailpit(fake_smtp):
    email_mod.send_email("ops@example.com", "subject", "body")

    conn = _FakeSMTP.instances[0]
    assert (conn.host, conn.port) == ("localhost", 1025)
    assert conn.started_tls is False
    assert conn.login_args is None
    assert _FakeSMTPSSL.instances == []


def test_reads_config_at_call_time_not_import_time(fake_smtp, monkeypatch):
    # A .env loaded after this module is imported must still take effect.
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")

    email_mod.send_email("ops@example.com", "subject", "body")

    conn = _FakeSMTP.instances[0]
    assert (conn.host, conn.port) == ("smtp.example.com", 587)


def test_starttls_and_login_when_credentials_configured(fake_smtp, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")

    email_mod.send_email("ops@example.com", "subject", "body")

    conn = _FakeSMTP.instances[0]
    assert conn.started_tls is True
    assert conn.login_args == ("alerts@example.com", "app-password")


def test_implicit_tls_uses_smtp_ssl_and_skips_starttls(fake_smtp, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_SSL", "1")
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    email_mod.send_email("ops@example.com", "subject", "body")

    assert _FakeSMTP.instances == []
    conn = _FakeSMTPSSL.instances[0]
    assert (conn.host, conn.port) == ("smtp.example.com", 465)
    # starttls on an already-encrypted socket is an error, not a no-op.
    assert conn.started_tls is False
    assert conn.login_args == ("alerts@example.com", "secret")


def test_no_login_when_only_user_is_set(fake_smtp, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")

    email_mod.send_email("ops@example.com", "subject", "body")

    assert _FakeSMTP.instances[0].login_args is None


def test_message_carries_from_to_subject_and_body(fake_smtp, monkeypatch):
    monkeypatch.setenv("SMTP_FROM", "MaintainIQ <alerts@example.com>")

    email_mod.send_email("ops@example.com", "CRITICAL: m1", "machine down")

    message = _FakeSMTP.instances[0].sent[0]
    assert message["From"] == "MaintainIQ <alerts@example.com>"
    assert message["To"] == "ops@example.com"
    assert message["Subject"] == "CRITICAL: m1"
    assert "machine down" in message.get_content()


def test_timeout_is_configurable(fake_smtp, monkeypatch):
    monkeypatch.setenv("SMTP_TIMEOUT", "30")

    email_mod.send_email("ops@example.com", "subject", "body")

    assert _FakeSMTP.instances[0].timeout == 30.0
