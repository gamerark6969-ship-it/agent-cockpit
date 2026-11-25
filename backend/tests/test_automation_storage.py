import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi import HTTPException

from app.automation.storage import media_file_response, media_url, run_directory, verify_media_signature


class AutomationStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_media_urls_are_expiring_and_run_scoped(self):
        url = media_url("00000000-0000-0000-0000-000000000001")
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        expiry = int(query["expires"][0])
        signature = query["sig"][0]
        run_id = "00000000-0000-0000-0000-000000000001"
        self.assertTrue(verify_media_signature(run_id, expiry, signature))
        self.assertFalse(verify_media_signature("00000000-0000-0000-0000-000000000002", expiry, signature))
        self.assertFalse(verify_media_signature(run_id, 1, signature))

    async def test_run_owned_path_and_byte_ranges(self):
        with TemporaryDirectory() as temp:
            from unittest.mock import patch
            from app.automation.config import automation_settings

            with patch.object(automation_settings, "storage_dir", Path(temp)):
                directory = run_directory("00000000-0000-0000-0000-000000000003")
                video = directory / "short.mp4"
                video.write_bytes(b"0123456789")
                request = SimpleNamespace(headers={"range": "bytes=2-5"})
                response = await media_file_response(video, request)
                body = b"".join([part async for part in response.body_iterator])
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.headers["content-range"], "bytes 2-5/10")
                self.assertEqual(body, b"2345")
                with self.assertRaises(ValueError):
                    run_directory("../other")
                with self.assertRaises(HTTPException) as raised:
                    await media_file_response(video, SimpleNamespace(headers={"range": "bytes=20-30"}))
                self.assertEqual(raised.exception.status_code, 416)


if __name__ == "__main__":
    unittest.main()
