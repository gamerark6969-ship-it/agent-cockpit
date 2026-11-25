import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.preview_security import preview_signature, signed_preview_path, verify_preview_signature


class PreviewSecurityTests(unittest.TestCase):
    def test_signed_path_contains_expiry_and_signature(self):
        with patch("app.preview_security.settings.PREVIEW_URL_TTL_S", 60):
            path = signed_preview_path("artifact-id", now=100)
        self.assertIn("expires=160", path)
        self.assertIn("signature=", path)

    def test_valid_signature_is_accepted(self):
        expires = int(time.time()) + 60
        verify_preview_signature("artifact-id", expires, preview_signature("artifact-id", expires))

    def test_invalid_signature_is_rejected(self):
        expires = int(time.time()) + 60
        with self.assertRaises(HTTPException) as raised:
            verify_preview_signature("artifact-id", expires, "invalid")
        self.assertEqual(raised.exception.status_code, 403)

    def test_expired_signature_is_rejected(self):
        expires = int(time.time()) - 1
        with self.assertRaises(HTTPException) as raised:
            verify_preview_signature("artifact-id", expires, preview_signature("artifact-id", expires))
        self.assertEqual(raised.exception.status_code, 410)
