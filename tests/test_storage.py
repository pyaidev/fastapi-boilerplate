import uuid
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.infrastructure.storage import S3Storage


def test_storage_requires_configuration(settings):
    with pytest.raises(ValueError):
        S3Storage(settings)


def test_private_presigned_policies(settings, monkeypatch):
    settings.s3_bucket = "test-private-bucket"
    sdk = MagicMock()
    sdk.generate_presigned_post.return_value = {"url": "https://example.com", "fields": {}}
    sdk.generate_presigned_url.return_value = "https://example.com/signed"
    monkeypatch.setattr("app.infrastructure.storage.boto3.client", lambda *a, **kw: sdk)
    storage = S3Storage(settings)
    owner = uuid.uuid4()
    policy = storage.upload_policy(owner, "image/png", 4096)
    assert policy["key"].startswith(f"private/{owner}/")
    kwargs = sdk.generate_presigned_post.call_args.kwargs
    assert kwargs["Conditions"] == [{"Content-Type": "image/png"}, ["content-length-range", 1, 4096]]
    assert kwargs["ExpiresIn"] == 300
    assert storage.download_url(owner, policy["key"]) == "https://example.com/signed"
    for key in [f"private/{uuid.uuid4()}/file", f"private/{owner}/../file"]:
        with pytest.raises(AppError):
            storage.download_url(owner, key)
    for content_type, size in [("text/html", 1024), ("image/png", 0), ("image/png", 10_485_761)]:
        with pytest.raises(AppError):
            storage.upload_policy(owner, content_type, size)
