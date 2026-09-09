import json
import logging
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from functools import lru_cache

from celery import Celery
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import EmailOutbox

settings = get_settings()
celery_app = Celery("fastapi", broker=settings.celery_broker_url.get_secret_value())
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_soft_time_limit=240,
    task_time_limit=300,
    broker_connection_retry_on_startup=True,
    beat_schedule={"email-outbox": {"task": "outbox.deliver", "schedule": 10.0}},
)


@lru_cache
def worker_sessions() -> sessionmaker:
    # Created lazily inside each worker process, never inherited across a fork.
    engine = create_engine(
        settings.database_url.get_secret_value().replace("+asyncpg", "+psycopg"),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        hide_parameters=True,
    )
    return sessionmaker(engine, expire_on_commit=False)


def deliver_batch() -> int:
    delivered = 0
    for _ in range(20):
        with worker_sessions().begin() as session:
            message = session.scalar(
                select(EmailOutbox)
                .where(
                    EmailOutbox.sent_at.is_(None),
                    EmailOutbox.available_at <= datetime.now(UTC),
                    EmailOutbox.attempts < 10,
                )
                .order_by(EmailOutbox.available_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if message is None:
                break
            try:
                payload = json.loads(
                    Fernet(settings.outbox_encryption_key.get_secret_value().encode()).decrypt(
                        message.encrypted_payload.encode()
                    )
                )
                email = EmailMessage()
                email["Subject"], email["From"], email["To"] = (
                    payload["subject"],
                    settings.email_from,
                    message.recipient,
                )
                email["Message-ID"] = f"<{message.id}@{settings.app_name}>"
                email.set_content(payload["body"])
                with smtplib.SMTP(settings.email_host, settings.email_port, timeout=10) as smtp:
                    if settings.email_use_tls:
                        smtp.starttls(context=ssl.create_default_context())
                    if settings.email_user:
                        smtp.login(settings.email_user, settings.email_password.get_secret_value())
                    smtp.send_message(email)
                message.sent_at = datetime.now(UTC)
                message.encrypted_payload = ""  # Do not retain reset secrets after delivery.
                delivered += 1
            except Exception as exc:
                message.attempts += 1
                message.available_at = datetime.now(UTC) + timedelta(seconds=min(3600, 2**message.attempts))
                logging.getLogger(__name__).warning(
                    "Outbox delivery failed", extra={"exception_type": type(exc).__name__}
                )
    return delivered


@celery_app.task(name="outbox.deliver")
def deliver_outbox() -> int:
    return deliver_batch()
