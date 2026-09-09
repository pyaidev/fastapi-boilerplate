import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import access_token, decode_access, hash_password, verify_password


async def test_argon2_hash_and_verify():
    hashed = await hash_password("a-long-and-safe-password")
    assert hashed.startswith("$argon2id$")
    assert await verify_password("a-long-and-safe-password", hashed)
    assert not await verify_password("incorrect", hashed)


def test_access_token_validates_type_signature_and_expiry(settings):
    token = access_token(uuid.uuid4(), uuid.uuid4(), 0, settings)
    assert decode_access(token, settings)["type"] == "access"
    payload = jwt.decode(token, options={"verify_signature": False})
    for changes in [
        {"type": "refresh"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"aud": "other-app"},
        {"sid": 123},
        {"ver": True},
    ]:
        invalid = jwt.encode(
            {**payload, **changes}, settings.secret_key.get_secret_value(), algorithm="HS256"
        )
        with pytest.raises(AppError):
            decode_access(invalid, settings)
    with pytest.raises(AppError):
        decode_access(token + "tampered", settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"secret_key": "short"},
        {"allowed_hosts": ["*"]},
        {"database_url": "sqlite:///test.db"},
        {"redis_url": ""},
        {"rate_login": "0/minute"},
        {"cors_origins": ["*"]},
        {"app_env": "production", "docs_enabled": True},
    ],
)
def test_invalid_settings_fail_early(settings, overrides):
    values = {**settings.model_dump(), **overrides}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_valid_production_configuration(settings):
    values = {
        **settings.model_dump(),
        "app_env": "production",
        "docs_enabled": False,
        "public_base_url": "https://api.example.com",
        "email_use_tls": True,
    }
    assert Settings(_env_file=None, **values).app_env == "production"
