"""Servicio de almacenamiento S3 compatible (MinIO)."""

from __future__ import annotations

import uuid

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import settings


def _get_client():
    """Crea un cliente S3 apuntando a MinIO."""
    endpoint = settings.minio_endpoint
    if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        endpoint = f"https://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _ensure_bucket():
    """Crea el bucket si no existe."""
    client = _get_client()
    try:
        client.head_bucket(Bucket=settings.minio_bucket)
    except Exception:
        client.create_bucket(Bucket=settings.minio_bucket)


def upload_file(file_bytes: bytes, content_type: str) -> str:
    """
    Sube un archivo a MinIO.
    Retorna el filename (sin path) para servir via API proxy.
    """
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(content_type, ".bin")

    filename = f"{uuid.uuid4().hex}{ext}"
    _ensure_bucket()

    client = _get_client()
    client.put_object(
        Bucket=settings.minio_bucket,
        Key=filename,
        Body=file_bytes,
        ContentType=content_type,
    )
    return filename


def get_file(filename: str) -> tuple[bytes, str] | None:
    """
    Obtiene un archivo de MinIO.
    Retorna (bytes, content_type) o None si no existe.
    """
    client = _get_client()
    try:
        obj = client.get_object(Bucket=settings.minio_bucket, Key=filename)
        return (obj["Body"].read(), obj["ContentType"])
    except ClientError:
        return None
