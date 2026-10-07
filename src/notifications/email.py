"""Outgoing email via SMTP.

Works against both ends of the spectrum with no code change, only env:

* Mailpit (dev/demo — see docker-compose.yml): no auth, no TLS. This is the
  default, so `docker compose up` keeps working with an empty .env.
* A real provider (Gmail / Outlook / SES / SendGrid): STARTTLS on :587 or
  implicit TLS on :465, with a username + password/app-password.

Plain stdlib smtplib/email.mime — a third-party mail library adds nothing
over this for either case.

Config is read on every call rather than at import, so a .env loaded after
this module is first imported (and an env change between sends) still takes
effect; the alternative silently pins whatever was set at import time.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _config() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", "localhost"),
        "port": int(os.environ.get("SMTP_PORT", "1025")),
        "sender": os.environ.get("SMTP_FROM", "alerts@maintainiq.local"),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        # Implicit TLS (SMTPS, usually :465) wraps the socket from the start;
        # STARTTLS (usually :587) upgrades a plaintext one. They are mutually
        # exclusive — calling starttls() on an SMTP_SSL socket is an error.
        "use_ssl": _flag("SMTP_SSL"),
        "use_starttls": _flag("SMTP_STARTTLS"),
        "timeout": float(os.environ.get("SMTP_TIMEOUT", "5")),
    }


def send_email(to: str, subject: str, body_text: str) -> None:
    """Send one plaintext email. Raises OSError/smtplib.SMTPException on
    failure — callers (src/notifications/dispatch.py) decide whether a
    delivery failure is fatal or just a 'failed' notifications row."""
    config = _config()

    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body_text)

    if config["use_ssl"]:
        opener = lambda: smtplib.SMTP_SSL(  # noqa: E731 - one-line transport switch
            config["host"], config["port"], timeout=config["timeout"]
        )
    else:
        opener = lambda: smtplib.SMTP(  # noqa: E731
            config["host"], config["port"], timeout=config["timeout"]
        )

    with opener() as smtp:
        if config["use_starttls"] and not config["use_ssl"]:
            smtp.starttls(context=ssl.create_default_context())
        if config["user"] and config["password"]:
            smtp.login(config["user"], config["password"])
        smtp.send_message(message)
