"""Run integration tests in a fresh database, leaving the application database intact."""

import os
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import get_settings

settings = get_settings()
url = make_url(settings.database_url.get_secret_value())
redis_url = settings.redis_url.get_secret_value().rsplit("/", 1)[0] + "/15"
if "--local" in sys.argv:
    url = url.set(host="127.0.0.1", port=int(os.getenv("LOCAL_DB_PORT", "55432")))
    redis_url = f"redis://127.0.0.1:{os.getenv('LOCAL_REDIS_PORT', '56379')}/15"
name = "fastapi_test_" + uuid.uuid4().hex[:16]
admin_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
with psycopg.connect(admin_url, autocommit=True) as connection:
    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
try:
    test_url = url.set(database=name).render_as_string(hide_password=False)
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": test_url,
        "TEST_DATABASE_URL": test_url,
        "REDIS_URL": redis_url,
        "TEST_REDIS_URL": redis_url,
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True)
    subprocess.run([sys.executable, "-m", "alembic", "check"], env=environment, check=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *[arg for arg in sys.argv[1:] if arg != "--local"],
        ],
        env=environment,
    )
    exit_code = result.returncode
    if exit_code == 0:
        subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], env=environment, check=True)
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True)
finally:
    with psycopg.connect(admin_url, autocommit=True) as connection:
        # This exact database was created above with a random name in this invocation.
        connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
sys.exit(exit_code)
