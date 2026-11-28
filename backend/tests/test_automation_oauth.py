import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.automation.config import automation_settings
from app.automation.models import OAuthState, YouTubeCredential
from app.automation.youtube import authorization_url, oauth_callback
from app.crypto import decrypt_json
from app.models import Base


class _Response:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeGoogleClient:
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return _Response({"access_token": "ACCESS-TOKEN-PRIVATE", "refresh_token": "REFRESH-TOKEN-PRIVATE", "expires_in": 3600})

    async def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return _Response({"items": [{"id": "channel-123", "snippet": {"title": "Sample Channel"}}]})


class OAuthStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_state_is_hashed_short_lived_single_use_and_tokens_encrypted(self):
        with (
            patch.object(automation_settings, "youtube_client_id", "client-id"),
            patch.object(automation_settings, "youtube_client_secret", "client-secret"),
            patch.object(automation_settings, "youtube_redirect_uri", "https://app.test/api/automation/youtube/callback"),
        ):
            async with self.sessions() as session:
                url = await authorization_url(session)
            query = parse_qs(urlparse(url).query)
            raw_state = query["state"][0]
            self.assertEqual(query["access_type"], ["offline"])
            self.assertIn("youtube.readonly", query["scope"][0])
            async with self.sessions() as session:
                state = await session.get(OAuthState, hashlib.sha256(raw_state.encode()).hexdigest())
                self.assertIsNotNone(state)
                self.assertNotEqual(state.state_hash, raw_state)

            _FakeGoogleClient.requests = []
            with patch("app.automation.youtube.httpx.AsyncClient", _FakeGoogleClient):
                async with self.sessions() as session:
                    await oauth_callback(session, code="one-time-code", state=raw_state)
                async with self.sessions() as session:
                    credential = await session.get(YouTubeCredential, 1)
                    decoded = decrypt_json(credential.tokens_enc)
                    self.assertEqual(credential.channel_id, "channel-123")
                    self.assertEqual(decoded["refresh_token"], "REFRESH-TOKEN-PRIVATE")
                    self.assertNotIn("REFRESH-TOKEN-PRIVATE", credential.tokens_enc)
                    with self.assertRaises(HTTPException) as raised:
                        await oauth_callback(session, code="replay", state=raw_state)
                    self.assertEqual(raised.exception.status_code, 400)
            token_exchange = _FakeGoogleClient.requests[0][1]
            self.assertEqual(token_exchange["data"]["code"], "one-time-code")
            self.assertNotIn("client-secret", str(url))

    async def test_client_file_can_be_supplied_and_rotated_after_start(self):
        from app.automation.youtube import _access_token, GOOGLE_AUTH, GOOGLE_TOKEN
        from app.crypto import encrypt_json
        with TemporaryDirectory() as directory:
            path = Path(directory) / "client.json"
            with (
                patch.object(automation_settings, "youtube_client_file", path),
                patch.object(automation_settings, "youtube_client_id", None),
                patch.object(automation_settings, "youtube_client_secret", None),
                patch.object(automation_settings, "youtube_redirect_uri", "http://localhost/callback"),
                patch("app.automation.youtube.httpx.AsyncClient", _FakeGoogleClient),
            ):
                self.assertFalse(automation_settings.youtube_configured)
                for content in [b"incomplete", b'{"installed":{"client_id":"i","client_secret":"s"}}', b'{"web":null}']:
                    path.write_bytes(content)
                    self.assertFalse(automation_settings.youtube_configured)
                web = {"client_id":"file-id", "client_secret":"file-secret", "auth_uri":"https://invalid.test",
                       "token_uri":"https://invalid.test", "redirect_uris":["https://invalid.test"]}
                path.write_text(json.dumps({"web":web}))
                self.assertTrue(automation_settings.youtube_configured)
                with patch.object(automation_settings, "youtube_client_id", "env"), patch.object(automation_settings, "youtube_client_secret", "env-secret"):
                    self.assertEqual(automation_settings.youtube_credentials(), ("env","env-secret"))
                async with self.sessions() as session:
                    url = await authorization_url(session)
                    self.assertEqual(url.split("?")[0], GOOGLE_AUTH)
                    query = parse_qs(urlparse(url).query)
                    self.assertEqual(query["client_id"], ["file-id"])
                    self.assertEqual(query["redirect_uri"], ["http://localhost/callback"])
                    _FakeGoogleClient.requests = []
                    await oauth_callback(session, code="fixture", state=query["state"][0])
                    self.assertEqual(_FakeGoogleClient.requests[0][0], GOOGLE_TOKEN)
                    self.assertEqual(_FakeGoogleClient.requests[0][1]["data"]["client_secret"], "file-secret")
                    record = await session.get(YouTubeCredential, 1)
                    record.tokens_enc = encrypt_json({"refresh_token":"fixture"})
                    await session.commit()
                    web["client_secret"] = "rotated"
                    path.write_text(json.dumps({"web":web}))
                    await _access_token(session)
                    self.assertEqual(_FakeGoogleClient.requests[-1][0], GOOGLE_TOKEN)
                    self.assertEqual(_FakeGoogleClient.requests[-1][1]["data"]["client_secret"], "rotated")

    async def test_shared_worker_can_refresh_uploaded_client_without_its_own_callback(self):
        from app.automation.models import YouTubeClient
        from app.automation.youtube import _access_token, GOOGLE_TOKEN
        from app.crypto import encrypt_json
        with (
            patch.object(automation_settings, "youtube_client_id", None),
            patch.object(automation_settings, "youtube_client_secret", None),
            patch.object(automation_settings, "youtube_client_file", None),
            patch.object(automation_settings, "youtube_redirect_uri", None),
            patch("app.automation.youtube.httpx.AsyncClient", _FakeGoogleClient),
        ):
            async with self.sessions() as session:
                session.add(YouTubeClient(id=1, config_enc=encrypt_json({"client_id":"stored-id","client_secret":"stored-secret"})))
                session.add(YouTubeCredential(id=1, tokens_enc=encrypt_json({"refresh_token":"refresh"})))
                await session.commit()
            async with self.sessions() as session:
                _FakeGoogleClient.requests = []
                await _access_token(session)
                endpoint, request = _FakeGoogleClient.requests[0]
                self.assertEqual(endpoint, GOOGLE_TOKEN)
                self.assertEqual(request["data"]["client_id"], "stored-id")
                self.assertEqual(request["data"]["client_secret"], "stored-secret")

    async def test_authorization_failure_does_not_reflect_provider_error_body(self):
        class FailingClient(_FakeGoogleClient):
            async def post(self, url, **kwargs):
                raise RuntimeError("private-refresh-token-in-error")

        with (
            patch.object(automation_settings, "youtube_client_id", "client-id"),
            patch.object(automation_settings, "youtube_client_secret", "client-secret"),
            patch.object(automation_settings, "youtube_redirect_uri", "https://app.test/callback"),
        ):
            async with self.sessions() as session:
                url = await authorization_url(session)
            raw_state = parse_qs(urlparse(url).query)["state"][0]
            with patch("app.automation.youtube.httpx.AsyncClient", FailingClient):
                async with self.sessions() as session:
                    with self.assertRaises(HTTPException) as raised:
                        await oauth_callback(session, code="code", state=raw_state)
            self.assertNotIn("private-refresh-token-in-error", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
