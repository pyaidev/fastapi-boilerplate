"""Operator commands. Passwords are read from the terminal, never argv."""

import argparse
import asyncio
import getpass
import sys
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.security import hash_password
from app.infrastructure.database import Database
from app.models import EmailOutbox, User
from app.modules.auth.schemas import Register
from app.modules.auth.service import audit


async def create_admin(database: Database, email: str, name: str, password: str) -> uuid.UUID:
    data = Register(email=email, name=name, password=password)
    async with database.sessions() as session:
        user = User(
            email=str(data.email).lower(),
            name=data.name.strip(),
            password_hash=await hash_password(data.password.get_secret_value()),
            role="admin",
        )
        session.add(user)
        await session.flush()
        audit(session, user.id, "admin.created", uuid.uuid4().hex)
        await session.commit()
        return user.id


async def command(args: argparse.Namespace) -> None:
    database = Database(get_settings())
    try:
        if args.command == "create-admin":
            if not sys.stdin.isatty():
                raise ValueError("Use an interactive terminal; passwords are not accepted through argv")
            password = getpass.getpass("Password (12–128 characters): ")
            if password != getpass.getpass("Confirm password: "):
                raise ValueError("Passwords do not match")
            identifier = await create_admin(database, args.email, args.name, password)
            print(f"Created administrator {identifier}")
        elif args.command == "outbox-status":
            async with database.sessions() as session:
                pending = await session.scalar(
                    select(func.count()).select_from(EmailOutbox).where(EmailOutbox.sent_at.is_(None))
                )
                failed = await session.scalar(
                    select(func.count())
                    .select_from(EmailOutbox)
                    .where(EmailOutbox.sent_at.is_(None), EmailOutbox.attempts >= 10)
                )
                print(f"Pending (including dead letters): {pending}; dead letters: {failed}")
                rows = await session.scalars(
                    select(EmailOutbox)
                    .where(EmailOutbox.sent_at.is_(None), EmailOutbox.attempts >= 10)
                    .order_by(EmailOutbox.created_at)
                    .limit(20)
                )
                for row in rows:
                    print(f"{row.id} attempts={row.attempts} created={row.created_at.isoformat()}")
        elif args.command == "retry-email":
            async with database.sessions() as session:
                message = await session.get(EmailOutbox, args.id, with_for_update=True)
                if not message or message.sent_at or message.attempts < 10:
                    raise ValueError("Only an existing, unsent dead letter can be retried")
                message.attempts, message.available_at = 0, datetime.now(UTC)
                await session.commit()
                print(f"Queued dead letter {message.id}; expired reset links remain invalid")
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create-admin", help="Create a new admin; never promotes an existing account"
    )
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="Administrator")
    commands.add_parser("outbox-status")
    retry = commands.add_parser("retry-email")
    retry.add_argument("id", type=uuid.UUID)
    args = parser.parse_args()
    try:
        asyncio.run(command(args))
    except IntegrityError:
        parser.exit(1, "Account already exists; no privileges were changed.\n")
    except ValidationError:
        parser.exit(1, "Invalid email, name or password length.\n")
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
