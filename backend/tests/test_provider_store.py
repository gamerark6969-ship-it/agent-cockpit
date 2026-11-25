import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.models import build_single_model, list_pa_models
from app.agent.provider_store import refresh_provider_routes, runtime_routes
from app.connectors import upsert, to_out
from app.models import Base, Connector


class ProviderStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        runtime_routes.clear()
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
