"""Persistent filesystem media and narrowly scoped signed playback URLs."""

import hashlib
import hmac
import mimetypes
import time
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from ..config import settings as app_settings
from .config import automation_settings


def media_root() -> Path:
    root = automation_settings.storage_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_directory(run_id: str) -> Path:
    if not run_id or any(char not in "0123456789abcdef-" for char in run_id.lower()):
        raise ValueError("invalid run identifier")
    directory = (media_root() / run_id).resolve()
    if directory.parent != media_root():
        raise ValueError("invalid run directory")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _signature(run_id: str, expires: int) -> str:
    secret = (app_settings.CONNECTOR_SECRET or app_settings.APP_TOKEN).encode("utf-8")
    payload = f"automation-media:{run_id}:{expires}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def media_url(run_id: str, render_data: dict | None = None) -> str:
    if automation_settings.s3_configured:
        return _s3().generate_presigned_url("get_object", Params={
            "Bucket": automation_settings.s3_bucket, "Key": (render_data or {}).get("object_key", f"runs/{run_id}/video.mp4")},
            ExpiresIn=min(automation_settings.media_url_ttl_s, 3600))
    expires = int(time.time()) + automation_settings.media_url_ttl_s
    path = f"/api/automation/media/{run_id}?expires={expires}&sig={_signature(run_id, expires)}"
    origin = (automation_settings.public_base_url or app_settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{origin}{path}"


def _s3():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=automation_settings.s3_endpoint,
        aws_access_key_id=automation_settings.s3_access_key, aws_secret_access_key=automation_settings.s3_secret_key,
        region_name=automation_settings.s3_region, config=Config(signature_version="s3v4", s3={"addressing_style":"path"}))


async def archive_media(run_id: str, result: dict) -> dict:
    if not automation_settings.s3_configured:
        return {}
    client = _s3()
    from sqlalchemy import select
    from ..db import SessionLocal
    from .models import VideoRun
    async with SessionLocal() as session:
        protected = set((await session.execute(select(VideoRun.id).where(
            VideoRun.status.in_(("queued", "running", "review", "uploading", "failed"))
        ))).scalars().all())
    existing = await asyncio.to_thread(_prune_and_measure, client, protected)
    incoming = sum(Path(result[field]).stat().st_size for field in ("path", "thumbnail_path"))
    if existing + incoming > automation_settings.media_storage_limit_mb * 1024 * 1024:
        raise RuntimeError("Preview storage limit reached; remove old media before retrying")
    def checksum():
        digest = hashlib.sha256()
        with Path(result["path"]).open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    content_hash = await asyncio.to_thread(checksum)
    prefix = f"runs/{run_id}/{content_hash[:16]}"
    for field, mime in (("path", "video/mp4"), ("thumbnail_path", "image/jpeg")):
        path = Path(result[field])
        await asyncio.to_thread(client.upload_file, str(path), automation_settings.s3_bucket,
            f"{prefix}/{path.name}", ExtraArgs={"ContentType":mime})
    return {"object_key":f"{prefix}/video.mp4", "content_sha256":content_hash}


def _prune_and_measure(client, protected_runs: set[str]) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=automation_settings.media_retention_days)
    total = 0
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=automation_settings.s3_bucket, Prefix="runs/"):
        old = []
        for item in page.get("Contents", []):
            run_id = item["Key"].split("/")[1] if item["Key"].count("/") >= 2 else ""
            if item["LastModified"] < cutoff and run_id not in protected_runs:
                old.append({"Key": item["Key"]})
            else:
                total += item["Size"]
        if old:
            client.delete_objects(Bucket=automation_settings.s3_bucket, Delete={"Objects":old})
    return total


async def restore_media(run_id: str, render_data: dict | None = None) -> Path:
    target = run_directory(run_id) / "video.mp4"
    if not target.is_file():
        if not automation_settings.s3_configured:
            raise RuntimeError("Media is missing; configure persistent object storage")
        partial = target.with_suffix(".download")
        try:
            await asyncio.to_thread(_s3().download_file, automation_settings.s3_bucket,
                (render_data or {}).get("object_key", f"runs/{run_id}/video.mp4"), str(partial))
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    return target


def verify_media_signature(run_id: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()) or expires > int(time.time()) + 7 * 86400:
        return False
    return hmac.compare_digest(_signature(run_id, expires), signature)


async def media_file_response(path: Path, request: Request):
    path = path.resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    range_header = request.headers.get("range")
    start, end = 0, size - 1
    status = 200
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
    if range_header:
        try:
            unit, part = range_header.split("=", 1)
            if unit != "bytes" or "," in part:
                raise ValueError
            left, right = part.split("-", 1)
            if left:
                start = int(left)
                end = int(right) if right else size - 1
            else:
                suffix = int(right)
                start = max(0, size - suffix)
            if start < 0 or end < start or start >= size:
                raise ValueError
            end = min(end, size - 1)
        except (ValueError, TypeError):
            raise HTTPException(status_code=416, detail="Invalid byte range", headers={"Content-Range": f"bytes */{size}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)

    async def chunks():
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(chunks(), status_code=status, media_type=content_type, headers=headers)
