import logging
import uuid

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger("utils")


def get_s3_client():
    """
    Returns a configured boto3 S3 client.
    Uses credentials from Django settings loaded from .env
    """
    return boto3.client(
        "s3",
        region_name           = settings.AWS_S3_REGION,
        aws_access_key_id     = settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY,
    )


def generate_s3_key(meeting_id: str, filename: str) -> str:
    """
    Generate a unique safe S3 key.
    Format: meetings/<meeting_id>/audio/<uuid>.<ext>
    """
    ext = "mp3"
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in ("mp3", "mp4", "wav", "m4a", "webm", "mov"):
            ext = "mp3"
    return f"meetings/{meeting_id}/audio/{uuid.uuid4()}.{ext}"


def generate_presigned_upload_url(
    meeting_id: str,
    filename: str,
    content_type: str,
) -> dict | None:
    """
    Generate a presigned PUT URL for direct browser to S3 upload.
    Django never sees the file bytes.
    Returns dict with upload_url, s3_key, expires_in.
    Returns None if S3 call fails.
    """
    s3_key = generate_s3_key(meeting_id, filename)

    try:
        client     = get_s3_client()
        upload_url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket":      settings.AWS_STORAGE_BUCKET_NAME,
                "Key":         s3_key,
                "ContentType": content_type,
            },
            ExpiresIn=settings.AWS_PRESIGNED_UPLOAD_EXPIRY,
        )
        logger.info(
            "Presigned upload URL generated for meeting %s key %s",
            meeting_id,
            s3_key,
        )
        return {
            "upload_url": upload_url,
            "s3_key":     s3_key,
            "expires_in": settings.AWS_PRESIGNED_UPLOAD_EXPIRY,
        }

    except ClientError as exc:
        logger.error(
            "Failed to generate presigned upload URL for meeting %s: %s",
            meeting_id,
            exc,
        )
        return None


def generate_presigned_download_url(s3_key: str) -> str | None:
    """
    Generate a presigned GET URL for direct browser to S3 download.
    Django never serves the file bytes.
    Returns URL string or None if S3 call fails.
    """
    try:
        client       = get_s3_client()
        download_url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                "Key":    s3_key,
            },
            ExpiresIn=settings.AWS_PRESIGNED_DOWNLOAD_EXPIRY,
        )
        logger.info(
            "Presigned download URL generated for key %s",
            s3_key,
        )
        return download_url

    except ClientError as exc:
        logger.error(
            "Failed to generate presigned download URL for key %s: %s",
            s3_key,
            exc,
        )
        return None


def delete_s3_object(s3_key: str) -> bool:
    """
    Delete an object from S3.
    Returns True on success, False on failure.
    """
    try:
        client = get_s3_client()
        client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
        )
        logger.info("Deleted S3 object %s", s3_key)
        return True

    except ClientError as exc:
        logger.error(
            "Failed to delete S3 object %s: %s",
            s3_key,
            exc,
        )
        return False


def check_s3_object_exists(s3_key: str) -> bool:
    """
    Check if an object exists in S3.
    Used to verify client actually uploaded before saving key to DB.
    Returns True if exists, False if not found or error.
    """
    try:
        client = get_s3_client()
        client.head_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_key,
        )
        return True
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code == "404":
            logger.warning("S3 object not found: %s", s3_key)
        else:
            logger.error(
                "S3 head_object error for %s: %s",
                s3_key,
                exc,
            )
        return False