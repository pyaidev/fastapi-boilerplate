import uuid
from typing import Any

import boto3

from app.core.config import Settings
from app.core.errors import AppError


class S3Storage:
    """Private direct uploads; call synchronous SDK operations from a thread pool."""

    def __init__(self, settings: Settings):
        if not settings.s3_bucket:
            raise ValueError("Configure S3_BUCKET before using object storage")
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3", region_name=settings.s3_region, endpoint_url=settings.s3_endpoint_url or None
        )

    def upload_policy(
        self, owner: uuid.UUID, content_type: str, max_bytes: int = 10_485_760
    ) -> dict[str, Any]:
        if (
            content_type not in {"image/png", "image/jpeg", "application/pdf"}
            or not 1 <= max_bytes <= 10_485_760
        ):
            raise AppError(422, "invalid_upload", "Unsupported file type or size.")
        key = f"private/{owner}/{uuid.uuid4().hex}"
        policy = self.client.generate_presigned_post(
            self.bucket,
            key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_bytes]],
            ExpiresIn=300,
        )
        return {"key": key, **policy}

    def download_url(self, owner: uuid.UUID, key: str) -> str:
        if not key.startswith(f"private/{owner}/") or ".." in key:
            raise AppError(404, "not_found", "File not found.")
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=300,
        )
