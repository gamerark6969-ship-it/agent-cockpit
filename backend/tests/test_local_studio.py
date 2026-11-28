import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio import with_phone_ui


class LocalStudioTests(unittest.TestCase):
    def test_phone_links_refresh_without_intercepting_api_or_asset_errors(self):
        api = FastAPI()

        @api.get('/api/automation/youtube/callback')
        def callback():
            return {'callback': True}

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'index.html').write_text('<title>Studio</title>', encoding='utf-8')
            with TestClient(with_phone_ui(api, directory)) as client:
                for path in ['/studio?run=id', '/chats', '/reminders', '/projects/my-project']:
                    self.assertIn('<title>Studio</title>', client.get(path).text)
                self.assertEqual(client.get('/api/automation/youtube/callback').json(), {'callback': True})
                for path in ['/api/missing', '/s/missing', '/assets/missing.js', '/.env']:
                    self.assertEqual(client.get(path).status_code, 404)
