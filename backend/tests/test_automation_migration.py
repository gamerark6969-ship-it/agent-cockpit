import runpy
import unittest
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


class AutomationMigrationTests(unittest.TestCase):
    def test_fresh_database_upgrades_through_the_complete_revision_chain(self):
        import asyncio
        from tempfile import TemporaryDirectory
        from alembic import command
        from alembic.config import Config
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        with TemporaryDirectory() as directory:
            url = "sqlite+aiosqlite:///"+(Path(directory)/"fresh.db").as_posix()
            config = Config(str(Path(__file__).parents[1]/"alembic.ini"))
            config.set_main_option("script_location",str(Path(__file__).parents[1]/"migrations"))
            config.set_main_option("sqlalchemy.url",url)
            command.upgrade(config,"head")
            async def verify():
                engine = create_async_engine(url)
                try:
                    async with engine.connect() as connection:
                        self.assertEqual(await connection.scalar(text("select version_num from alembic_version")),"0007_project_chat_schema")
                        columns = await connection.run_sync(lambda c: {item["name"] for item in inspect(c).get_columns("automation_runs")})
                        self.assertTrue({"retry_count","next_attempt_at"}.issubset(columns))
                finally:
                    await engine.dispose()
            asyncio.run(verify())

    def test_legacy_project_upgrade_preserves_repos_and_allows_chat_threads(self):
        from sqlalchemy import text
        migration = runpy.run_path(str(Path(__file__).parents[1]/"migrations/versions/0007_project_chat_schema.py"))
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, repo_url TEXT NOT NULL)"))
                connection.execute(text("INSERT INTO projects VALUES ('existing','Existing repo','https://github.com/test/repo')"))
                with Operations.context(MigrationContext.configure(connection)):
                    migration["upgrade"]()
                    migration["upgrade"]()
                self.assertEqual(connection.execute(text("SELECT kind FROM projects WHERE id='existing'")).scalar(),"repo")
                connection.execute(text("INSERT INTO projects (id,name,kind,repo_url) VALUES ('chat','Chat','chat',NULL)"))
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM projects")).scalar(),2)
        finally:
            engine.dispose()

    def test_revision_chain_and_idempotent_table_creation(self):
        migration = runpy.run_path(
            str(Path(__file__).parents[1] / "migrations" / "versions" / "0003_video_automation.py")
        )
        self.assertEqual(migration["revision"], "0003_video_automation")
        self.assertEqual(migration["down_revision"], "0002_worker_leases")
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as connection:
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    migration["upgrade"]()
                    migration["upgrade"]()
                tables = set(inspect(connection).get_table_names())
                self.assertTrue(
                    {
                        "automation_series",
                        "automation_runs",
                        "automation_run_events",
                        "automation_youtube_credentials",
                        "automation_oauth_states",
                    }.issubset(tables)
                )
                indexes = {item["name"] for item in inspect(connection).get_indexes("automation_runs")}
                self.assertIn("ix_automation_runs_lease", indexes)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
