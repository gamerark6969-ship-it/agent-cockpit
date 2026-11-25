import json
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agent import models
from app.agent.opencode_runtime import isolated_environment, parse_output
from app.agent.opencode_runtime import FreeModelUnavailable
from app.config import settings


class ModelProviderTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        models.build_single_model.cache_clear()

    def test_free_output_requires_text_and_rejects_provider_errors(self):
        self.assertEqual(parse_output(json.dumps({"type":"text","part":{"text":"hello"}})), "hello")
        for raw in ("not json", '{"type":"error"}'):
            with self.assertRaises(RuntimeError):
                parse_output(raw)

    def test_child_environment_has_no_application_credentials(self):
        with patch.dict(os.environ, {"APP_TOKEN":"secret", "XINGCHI_API_KEY":"private", "GITHUB_PAT":"sensitive"}):
            env = isolated_environment(Path("isolated"))
        self.assertNotIn("APP_TOKEN", env)
        self.assertNotIn("XINGCHI_API_KEY", env)
        self.assertNotIn("GITHUB_PAT", env)
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["permission"], "deny")
        self.assertEqual(config["share"], "disabled")

    def test_xingchi_does_not_fall_back_when_unconfigured(self):
        models.build_single_model.cache_clear()
        with patch.object(settings,"XINGCHI_BASE_URL",None), patch.object(settings,"XINGCHI_API_KEY",None):
            self.assertIsNone(models.build_model_chain("xingchi2/gpt-6.1-sol"))

    def test_remote_worker_can_advertise_free_generation_models(self):
        with patch.object(settings,"OPENCODE_REMOTE_WORKER",True), patch("app.agent.models.shutil.which",return_value=None):
            self.assertIn("opencode/big-pickle",models.list_generation_models())

    async def test_default_generation_uses_free_runner(self):
        runner = AsyncMock(return_value="script")
        with patch("app.agent.models.shutil.which",return_value="opencode"), patch("app.agent.opencode_runtime.run_free_model",runner):
            self.assertEqual(await models.generate_text("write",system_prompt="system"),"script")
        self.assertEqual(runner.await_args.args[0],models.FREE_MODELS[0])

    async def test_unknown_model_is_not_sent_to_an_unrelated_provider(self):
        with self.assertRaises(RuntimeError):
            await models.generate_text("hello",model="unknown/paid-model")

    async def test_exhausted_free_quota_uses_cpu_backup_for_automatic_generation(self):
        with patch("app.agent.models.list_pa_models",return_value=[]), patch("app.agent.models.shutil.which",return_value="opencode"), \
             patch.object(settings,"LOCAL_LLM_ENABLED",True), \
             patch("app.agent.opencode_runtime.run_free_model",AsyncMock(side_effect=FreeModelUnavailable("quota exhausted"))), \
             patch("app.agent.local_inference.generate_local",AsyncMock(return_value="CPU response")):
            self.assertEqual(await models.generate_text("write a story"),"CPU response")

    def test_neon_connection_string_is_compatible_with_asyncpg(self):
        from app.config import Settings
        value = Settings(DATABASE_URL="postgresql://user:pass@host/db?sslmode=require&channel_binding=require").DATABASE_URL
        self.assertTrue(value.startswith("postgresql+asyncpg://"))
        self.assertIn("ssl=require",value)
        self.assertNotIn("channel_binding",value)
