import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import routes
from app.models import Artifact, Base, Project, Task


class ServeSiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            session.add(Project(id="project-id", name="project"))
            session.add(Task(id="task-id", project_id="project-id", prompt="p"))
            session.add_all(
                [
                    Artifact(
                        id="site-artifact",
                        task_id="task-id",
                        kind="site",
                        filename="index.html",
                        mime="text/html",
                        data=b"<html></html>",
                    ),
                    Artifact(
                        id="screenshot-artifact",
                        task_id="task-id",
                        kind="screenshot",
                        filename="shot.png",
                        mime="image/png",
                        data=b"\x89PNG",
                    ),
                ]
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def serve(self, artifact_id):
        request = type(
            "Request", (), {"headers": {}, "client": type("Client", (), {"host": "1.2.3.4"})()}
        )()
        with (
            patch.object(routes, "SessionLocal", self.sessions),
            patch("app.api.routes.enforce_preview_rate_limit"),
            patch("app.api.routes.verify_preview_signature"),
        ):
            return await routes.serve_site(
                artifact_id,
                request,
                expires=9999999999,
                signature="irrelevant",
            )

    async def test_serves_site_artifact_with_hardening_headers(self):
        response = await self.serve("site-artifact")

        self.assertEqual(response.body, b"<html></html>")
        self.assertEqual(response.media_type, "text/html")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-robots-tag"], "noindex, nofollow")

    async def test_rejects_non_site_artifact_kinds(self):
        with self.assertRaises(HTTPException) as raised:
            await self.serve("screenshot-artifact")
        self.assertEqual(raised.exception.status_code, 404)

    async def test_rejects_unknown_artifact(self):
        with self.assertRaises(HTTPException) as raised:
            await self.serve("missing-artifact")
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
