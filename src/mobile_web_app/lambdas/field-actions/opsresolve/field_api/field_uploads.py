"""Field app uploads Lambda: POST /mobile-web-app/uploads.

Handler: opsresolve.field_api.field_uploads.handler

Issues a presigned S3 PUT URL for a photo/signature/assistant-image upload. The client PUTs
the bytes straight to S3, then passes the returned ``key`` to the complete (10) or assistant
(11) route. See API_requirements.md Section 9.

Bucket: ``opsresolve-dev-field-uploads`` (new, not yet created -- see resource.md task list).
This Lambda's role only needs ``s3:PutObject`` on ``field/*``, used to sign the URL (it never
calls PutObject itself).
"""
from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from ..common.config import settings
from ..common.logging import get_logger
from . import _common as c

logger = get_logger(__name__)

BUCKET = f"{settings.prefix}-field-uploads"
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIZE_BYTES = 3_750_000  # 3.75 MB -- the Bedrock Converse image limit (applies to every kind)
EXPIRES_IN_SECONDS = 300
_EXT_BY_CONTENT_TYPE = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_ALLOWED_KINDS = {"PHOTO", "SIGNATURE", "ASSISTANT_IMAGE"}


class _HttpError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _bad(message: str) -> _HttpError:
    return _HttpError(400, "BAD_REQUEST", message)


@lru_cache(maxsize=1)
def _s3():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("s3", region_name=settings.region)


def _presign(event: dict, technician_id: str) -> dict:
    body = c.parse_json_body(event)
    if body is None:
        raise _bad("Body must be a JSON object")

    job_id = body.get("jobId")
    kind = body.get("kind")
    content_type = body.get("contentType")
    size_bytes = body.get("sizeBytes")

    if not isinstance(job_id, str) or not job_id:
        raise _bad("jobId is required")
    if kind not in _ALLOWED_KINDS:
        raise _bad(f"kind must be one of {sorted(_ALLOWED_KINDS)}")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise _bad(f"contentType must be one of {sorted(ALLOWED_CONTENT_TYPES)}")
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        raise _bad("sizeBytes must be a positive integer")
    if size_bytes > MAX_SIZE_BYTES:
        raise _bad(f"sizeBytes exceeds the {MAX_SIZE_BYTES} byte limit")

    ext = _EXT_BY_CONTENT_TYPE[content_type]
    key = f"field/{job_id}/{kind}/{uuid.uuid4().hex}.{ext}"

    upload_url = _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=EXPIRES_IN_SECONDS,
    )

    return {
        "key": key,
        "uploadUrl": upload_url,
        "expiresInSeconds": EXPIRES_IN_SECONDS,
        "requiredHeaders": {"Content-Type": content_type},
    }


def handler(event: dict, context: Any = None) -> dict:
    route_key = event.get("routeKey", "")
    if route_key != "POST /mobile-web-app/uploads":
        return c.error(404, "NOT_FOUND", f"Unknown route {route_key}")

    try:
        technician_id, _username = c.technician_identity(event)
    except c.AuthError as exc:
        return c.error(exc.status, exc.code, exc.message)

    try:
        body = _presign(event, technician_id)
    except _HttpError as exc:
        return c.error(exc.status, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"field_uploads failed: {exc}")
        return c.error(500, "INTERNAL_ERROR", "Internal error")
    return c.ok(body)
