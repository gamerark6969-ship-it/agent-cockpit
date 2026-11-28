"""YouTube OAuth and resumable upload operations."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_json, encrypt_json
from .config import automation_settings as cfg
from .models import OAuthState, VideoRun, YouTubeCredential

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YT_API = "https://www.googleapis.com/youtube/v3"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
_TIMEOUT = httpx.Timeout(45.0, connect=15.0)


class UploadCancelled(RuntimeError):
    pass


def _safe_error(response: httpx.Response) -> str:
    # Google error bodies may contain user details or identifiers; never echo them.
    return f"YouTube request failed (HTTP {response.status_code})"


async def authorization_url(session: AsyncSession) -> str:
    credentials = cfg.youtube_credentials()
    if credentials is None:
        raise HTTPException(status_code=503, detail="YouTube OAuth is not configured")
    state = secrets.token_urlsafe(32)
    digest = hashlib.sha256(state.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    await session.execute(delete(OAuthState).where(OAuthState.expires_at < now))
    session.add(OAuthState(state_hash=digest, expires_at=now + timedelta(minutes=10)))
    await session.commit()
    params = {
        "client_id": credentials[0],
        "redirect_uri": cfg.youtube_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH}?{urlencode(params)}"


async def oauth_callback(session: AsyncSession, *, code: str, state: str) -> None:
    credentials = cfg.youtube_credentials()
    if credentials is None:
        raise HTTPException(status_code=503, detail="YouTube OAuth is not configured")
    digest = hashlib.sha256(state.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    result = await session.execute(
        delete(OAuthState).where(OAuthState.state_hash == digest, OAuthState.expires_at > now)
    )
    if result.rowcount != 1:
        await session.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    await session.commit()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.post(
                GOOGLE_TOKEN,
                data={
                    "code": code,
                    "client_id": credentials[0],
                    "client_secret": credentials[1],
                    "redirect_uri": cfg.youtube_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            token_data = response.json()
            channel_response = await client.get(
                f"{YT_API}/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            channel_response.raise_for_status()
            items = channel_response.json().get("items") or []
            if not items:
                raise HTTPException(status_code=400, detail="No YouTube channel is available for this account")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=502, detail="YouTube authorization could not be completed")
    prior = await session.get(YouTubeCredential, 1)
    old_tokens = decrypt_json(prior.tokens_enc) if prior else {}
    if prior and prior.channel_id != items[0].get("id"):
        old_tokens = {}
    refresh_token = token_data.get("refresh_token") or old_tokens.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=502, detail="YouTube did not provide offline access")
    token_data["refresh_token"] = refresh_token
    token_data["expires_at"] = int(now.timestamp()) + int(token_data.get("expires_in", 3600))
    channel = items[0]
    if prior is None:
        prior = YouTubeCredential(id=1, tokens_enc=encrypt_json(token_data))
        session.add(prior)
    prior.tokens_enc = encrypt_json(token_data)
    prior.channel_id = channel.get("id")
    prior.channel_title = (channel.get("snippet") or {}).get("title")
    await session.commit()


async def disconnect(session: AsyncSession) -> None:
    credential = await session.get(YouTubeCredential, 1)
    if credential is not None:
        await session.delete(credential)
        await session.commit()


async def _access_token(session: AsyncSession) -> str:
    credential = await session.get(YouTubeCredential, 1)
    if credential is None:
        raise RuntimeError("YouTube account is not connected")
    tokens = decrypt_json(credential.tokens_enc)
    if tokens.get("access_token") and int(tokens.get("expires_at", 0)) > int(datetime.now(timezone.utc).timestamp()) + 60:
        return tokens["access_token"]
    credentials = cfg.youtube_credentials()
    if not tokens.get("refresh_token") or credentials is None:
        raise RuntimeError("YouTube credentials need to be reconnected")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.post(
                GOOGLE_TOKEN,
                data={
                    "client_id": credentials[0],
                    "client_secret": credentials[1],
                    "refresh_token": tokens["refresh_token"],
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            refreshed = response.json()
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("YouTube access token refresh failed")
    tokens.update(refreshed)
    tokens["expires_at"] = int(datetime.now(timezone.utc).timestamp()) + int(refreshed.get("expires_in", 3600))
    credential.tokens_enc = encrypt_json(tokens)
    await session.commit()
    return tokens["access_token"]


def upload_metadata(run: VideoRun, series) -> dict:
    manifest = run.manifest or {}
    return {
        "snippet": {
            "title": run.title or manifest.get("title") or "Untitled video",
            "description": run.description or manifest.get("description") or "",
            "tags": manifest.get("tags", [])[:20],
            "categoryId": "22",
            "defaultLanguage": series.language,
        },
        "status": {
            "privacyStatus": getattr(series,"privacy","public"),
            "selfDeclaredMadeForKids": bool(series.made_for_kids),
            "containsSyntheticMedia": bool(series.contains_synthetic_media),
        },
    }


async def _query_session(client: httpx.AsyncClient, url: str, token: str, size: int) -> tuple[int, dict | None]:
    response = await client.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Length": "0", "Content-Range": f"bytes */{size}"},
    )
    if response.status_code in (200, 201):
        return size, response.json()
    if response.status_code == 308:
        value = response.headers.get("Range", "")
        try:
            return (int(value.rsplit("-", 1)[1]) + 1) if value else 0, None
        except ValueError:
            return 0, None
    if response.status_code in (404, 410):
        return -1, None
    if response.is_error:
        raise RuntimeError(_safe_error(response))
    return 0, None


async def upload_video(
    session: AsyncSession,
    run: VideoRun,
    series,
    path: Path,
    *,
    lease_owner: str,
    fence: int,
    cancel_requested=None,
) -> str | None:
    """Resume or reconcile one resumable upload; return the video id when done."""
    token = await _access_token(session)
    size = path.stat().st_size

    async def check_cancelled() -> None:
        if cancel_requested is not None and await cancel_requested():
            raise UploadCancelled("YouTube upload cancelled before completion")

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        await check_cancelled()
        credential_run = await session.get(VideoRun, run.id)
        upload_url = decrypt_json(credential_run.upload_session_enc).get("url") if credential_run else None
        offset = 0
        if upload_url:
            offset, completed = await _query_session(client, upload_url, token, size)
            if completed:
                return completed.get("id")
            if offset < 0:
                raise RuntimeError("Upload session expired; reconcile the channel before starting a replacement video")
        if not upload_url:
            try:
                response = await client.post(
                    YT_UPLOAD,
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=UTF-8",
                        "X-Upload-Content-Length": str(size),
                        "X-Upload-Content-Type": "video/mp4",
                    },
                    json=upload_metadata(run, series),
                )
                response.raise_for_status()
                upload_url = response.headers.get("Location")
                if not upload_url:
                    raise RuntimeError("YouTube did not return an upload session")
            except httpx.HTTPError as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    raise RuntimeError(_safe_error(exc.response))
                raise RuntimeError("YouTube upload session could not be created")
            persisted = await session.execute(
                update(VideoRun)
                .execution_options(synchronize_session=False)
                .where(
                    VideoRun.id == run.id,
                    VideoRun.lease_owner == lease_owner,
                    VideoRun.fence == fence,
                    VideoRun.lease_expires_at > datetime.now(timezone.utc),
                )
                .values(
                    upload_session_enc=encrypt_json({"url": upload_url}),
                    upload_session_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
            if persisted.rowcount != 1:
                await session.rollback()
                raise RuntimeError("YouTube upload lease expired")
            await session.commit()
            await session.refresh(credential_run, attribute_names=["upload_session_enc", "upload_session_expires_at"])
            offset = 0

        chunk_size = 8 * 1024 * 1024
        with path.open("rb") as video:
            while offset < size:
                await check_cancelled()
                video.seek(offset)
                chunk = video.read(min(chunk_size, size - offset))
                end = offset + len(chunk) - 1
                try:
                    response = await client.put(
                        upload_url,
                        content=chunk,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                        },
                    )
                except httpx.HTTPError:
                    # Session URL remains persisted; next worker asks YouTube for its offset.
                    raise RuntimeError("YouTube upload interrupted and will be reconciled")
                if response.status_code in (200, 201):
                    data = response.json()
                    return data.get("id")
                if response.status_code == 308:
                    value = response.headers.get("Range", "")
                    try:
                        next_offset = int(value.rsplit("-", 1)[1]) + 1 if value else 0
                    except ValueError:
                        next_offset = 0
                    if next_offset <= offset:
                        raise RuntimeError("YouTube upload made no progress")
                    offset = next_offset
                    continue
                if response.is_error:
                    raise RuntimeError(_safe_error(response))
                raise RuntimeError("YouTube returned an unexpected upload response")
    return None


async def processing_status(session: AsyncSession, video_id: str) -> str:
    token = await _access_token(session)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(f"{YT_API}/videos", params={"id":video_id,"part":"status,processingDetails"},
            headers={"Authorization":f"Bearer {token}"})
        if response.is_error:
            raise RuntimeError(_safe_error(response))
        items = response.json().get("items") or []
    if not items:
        return "pending"
    status = items[0].get("status", {}).get("uploadStatus")
    processing = items[0].get("processingDetails", {}).get("processingStatus")
    if status in ("failed", "rejected", "deleted") or processing in ("failed", "terminated"):
        raise RuntimeError("YouTube could not process the uploaded video")
    return "ready" if status == "processed" or processing == "succeeded" else "pending"
