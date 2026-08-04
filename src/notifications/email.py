"""Outgoing email via SMTP (Mailpit in dev/demo — see docker-compose.yml).

Plain stdlib smtplib/email.mime: Mailpit needs no auth or TLS, so a
third-party mail library would not add anything for this use case.
"""
import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "1025"))
SMTP_FROM = os.environ.get("SMTP_FROM", "alerts@maintainiq.local")


def send_email(to: str, subject: str, body_text: str) -> None:
    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body_text)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=5) as smtp:
        smtp.send_message(message)
