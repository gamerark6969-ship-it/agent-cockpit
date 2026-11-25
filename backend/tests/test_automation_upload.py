import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.automation.models import VideoRun, VideoSeries, YouTubeCredential
from app.automation.youtube import upload_video
from app.crypto import decrypt_json, encrypt_json
from app.models import Base


class _Response:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self.payload = payload or {}
        self.headers = headers or {}
        self.is_error = status_code >= 400

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.is_error:
            raise RuntimeError("unexpected response")


class _ScriptedClient:
    responses = []
    last = None

    def __init__(self, *args, **kwargs):
        self.calls = []
        type(self).last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return type(self).responses.pop(0)

    async def put(self, url, **kwargs):
        self.calls.append(("put", url, kwargs))
        return type(self).responses.pop(0)


class ResumableUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            session.add_all(
                [
                    VideoSeries(
                        id="series-upload", name="Test", brief="A sufficiently long brief for testing uploads.",
                        timezone="UTC", publish_times=["09:00"], enabled=True, publish_mode="auto",
                        language="en", voice="en-US-AriaNeural", made_for_kids=True,
                        contains_synthetic_media=True,
                    ),
                    VideoRun(
                        id="00000000-0000-0000-0000-000000000099", series_id="series-upload",
                        status="uploading", stage="upload", title="Short story", description="Test description",
                        manifest={"version": 1, "title": "Short story", "description": "Test description", "scenes": [], "tags": ["short"]},
                        lease_owner="worker", lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=2), fence=4,
                    ),
                    YouTubeCredential(
                        id=1,
                        tokens_enc=encrypt_json({"access_token": "ACCESS_PRIVATE", "refresh_token": "REFRESH_PRIVATE",
                                                 "expires_at": int(time.time()) + 3600}),
                        channel_id="channel",
                    ),
                ]
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _load(self, session):
        run = await session.get(VideoRun, "00000000-0000-0000-0000-000000000099")
        series = await session.get(VideoSeries, "series-upload")
        return run, series

    async def test_lost_chunk_resumes_from_youtube_range(self):
        _ScriptedClient.responses = [
            _Response(308, headers={"Range": "bytes=0-3"}),
            _Response(201, {"id": "video-resumed"}),
        ]
        with TemporaryDirectory() as temp:
            path = Path(temp) / "video.mp4"
            path.write_bytes(b"0123456789")
            with patch("app.automation.youtube.httpx.AsyncClient", _ScriptedClient):
                async with self.sessions() as session:
                    run, series = await self._load(session)
                    run.upload_session_enc = encrypt_json({"url": "https://upload.example/session/secret"})
                    await session.commit()
                    video_id = await upload_video(
                        session, run, series, path, lease_owner="worker", fence=4
                    )
                    sent = _ScriptedClient.last.calls[1][2]
                    self.assertEqual(video_id, "video-resumed")
                    self.assertEqual(sent["headers"]["Content-Range"], "bytes 4-9/10")
                    self.assertEqual(sent["content"], b"456789")
                    self.assertEqual(len(_ScriptedClient.last.calls), 2)

    async def test_completed_session_reconciles_without_creating_another_video(self):
        _ScriptedClient.responses = [_Response(200, {"id": "already-created"})]
        with TemporaryDirectory() as temp:
            path = Path(temp) / "video.mp4"
            path.write_bytes(b"video")
            with patch("app.automation.youtube.httpx.AsyncClient", _ScriptedClient):
                async with self.sessions() as session:
                    run, series = await self._load(session)
                    run.upload_session_enc = encrypt_json({"url": "https://upload.example/session/secret"})
                    await session.commit()
                    video_id = await upload_video(session, run, series, path, lease_owner="worker", fence=4)
                    self.assertEqual(video_id, "already-created")
                    self.assertEqual([call[0] for call in _ScriptedClient.last.calls], ["put"])

    async def test_new_upload_persists_encrypted_session_and_explicit_video_flags(self):
        _ScriptedClient.responses = [
            _Response(200, headers={"Location": "https://upload.example/session/private-session"}),
            _Response(201, {"id": "new-video"}),
        ]
        with TemporaryDirectory() as temp:
            path = Path(temp) / "video.mp4"
            path.write_bytes(b"video bytes")
            with patch("app.automation.youtube.httpx.AsyncClient", _ScriptedClient):
                async with self.sessions() as session:
                    run, series = await self._load(session)
                    video_id = await upload_video(session, run, series, path, lease_owner="worker", fence=4)
                    client = _ScriptedClient.last
                    metadata = client.calls[0][2]["json"]
                    self.assertEqual(video_id, "new-video")
                    self.assertEqual(metadata["status"]["privacyStatus"], "public")
                    self.assertTrue(metadata["status"]["selfDeclaredMadeForKids"])
                    self.assertTrue(metadata["status"]["containsSyntheticMedia"])
                    persisted = await session.get(VideoRun, run.id)
                    self.assertEqual(decrypt_json(persisted.upload_session_enc)["url"], "https://upload.example/session/private-session")
                    self.assertNotIn("private-session", persisted.upload_session_enc)

    async def test_expired_unknown_session_never_blindly_creates_a_duplicate(self):
        _ScriptedClient.responses = [_Response(404)]
        with TemporaryDirectory() as temp:
            path = Path(temp) / "video.mp4"
            path.write_bytes(b"video")
            with patch("app.automation.youtube.httpx.AsyncClient",_ScriptedClient):
                async with self.sessions() as session:
                    run,series = await self._load(session)
                    run.upload_session_enc = encrypt_json({"url":"https://upload.example/expired-session"})
                    await session.commit()
                    with self.assertRaisesRegex(RuntimeError,"reconcile"):
                        await upload_video(session,run,series,path,lease_owner="worker",fence=4)
                self.assertEqual([call[0] for call in _ScriptedClient.last.calls],["put"])


if __name__ == "__main__":
    unittest.main()
