import unittest
from unittest.mock import patch
import httpx
from pydantic_ai.models.openai import OpenAIChatModel

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.models import build_single_model, list_pa_models
from app.agent.provider_store import check_model_catalog, disabled_routes, refresh_provider_routes, runtime_routes
from app.config import settings
from app.connectors import get_config, update, upsert, to_out
from app.models import Base, Connector


class ProviderStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        runtime_routes.clear()
        disabled_routes.clear()
        build_single_model.cache_clear()
        await self.engine.dispose()

    async def test_saved_provider_is_encrypted_masked_and_used_by_model_factory(self):
        with patch("app.connectors.SessionLocal",self.sessions), patch("app.db.SessionLocal",self.sessions):
            connector = await upsert("freegg",config={"api_key":"test-private-value"})
            self.assertNotIn("test-private-value",connector.config_enc)
            self.assertNotIn("test-private-value",str(to_out(connector)))
            self.assertIn("freegg/gpt-6.1-sol",list_pa_models())
            self.assertEqual(build_single_model("freegg/gpt-6.1-sol").model_name,"gpt-6.1-sol")

    async def test_disabled_provider_is_removed_from_runtime_routes(self):
        with patch("app.connectors.SessionLocal",self.sessions), patch("app.db.SessionLocal",self.sessions):
            await upsert("agentrouter-openai",config={"api_key":"test-private-value"})
            async with self.sessions() as session:
                row = await session.scalar(select(Connector).where(Connector.kind == "agentrouter-openai"))
                row.enabled = False
                await session.commit()
            await refresh_provider_routes()
            self.assertNotIn("agentrouter-openai/gpt-6.1-sol",runtime_routes)

    async def test_provider_edits_preserve_keys_and_disabled_route_overrides_environment(self):
        with patch("app.connectors.SessionLocal", self.sessions), patch("app.db.SessionLocal", self.sessions), \
             patch.object(settings, "FREEGG_API_KEY", "old-env-key"):
            with self.assertRaises(ValueError):
                await upsert("freegg", config={})
            row = await upsert("freegg", config={"api_key": "saved-key"})
            await upsert("freegg", config={"api_key": "", "api_style": "chat"})
            self.assertEqual((await get_config("freegg"))["api_key"], "saved-key")
            self.assertIsInstance(build_single_model("freegg/gpt-6.1-sol"), OpenAIChatModel)
            with self.assertRaises(ValueError):
                await update(row.id, config={"base_url": "https://example.test/v1"})
            await update(row.id, enabled=False)
            self.assertNotIn("freegg/gpt-6.1-sol", list_pa_models())
            self.assertIsNone(build_single_model("freegg/gpt-6.1-sol"))

    async def test_catalog_check_uses_saved_key_and_keeps_provider_errors_private(self):
        config = {"base_url": "https://freegg.app/v1", "api_key": "saved-key"}
        for status, body, expected in (
            (200, {"data": [{"id": "gpt-6.1-sol"}]}, True),
            (200, {"data": [{"id": "other"}]}, False),
            (401, {"error": "sensitive-upstream-text"}, False),
        ):
            def handle(request):
                self.assertEqual(str(request.url), "https://freegg.app/v1/models")
                self.assertEqual(request.headers["Authorization"], "Bearer saved-key")
                return httpx.Response(status, json=body)
            client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            with patch("app.agent.provider_store.httpx.AsyncClient", return_value=client):
                result = await check_model_catalog("freegg", config)
            self.assertEqual(result["ok"], expected)
            self.assertNotIn("sensitive-upstream-text", result["detail"])
