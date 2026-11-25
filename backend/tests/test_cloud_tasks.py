import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import docker_sandbox
from app.models import Base, Project, Task, TaskControl, utcnow
from app.worker import Worker


class DurableControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.worker = Worker()
        async with self.sessions() as session:
            session.add(Project(id="project",name="Project"))
            session.add(Task(id="task",project_id="project",prompt="Create a page",status="running",lease_owner=self.worker.worker_id))
            session.add(Task(id="queued",project_id="project",prompt="Queued work",status="queued"))
            session.add(TaskControl(id="steer",task_id="task",kind="steer",message="Make it blue"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_control_is_acknowledged_with_durable_conversation_checkpoint(self):
        with patch("app.worker.SessionLocal",self.sessions):
            controls = await self.worker.read_steer_controls("task")
            self.assertEqual(controls,[("steer","Make it blue")])
            history = [{"kind":"request","parts":[{"part_kind":"user-prompt","content":"Make it blue"}]}]
            self.assertTrue(await self.worker.checkpoint_history("task",history,["steer"]))
            self.assertEqual(await self.worker.read_steer_controls("task"),[])
        async with self.sessions() as session:
            self.assertEqual((await session.get(Task,"task")).pa_history,history)
            self.assertIsNotNone((await session.get(TaskControl,"steer")).processed_at)

    async def test_stale_worker_cannot_acknowledge_a_phone_instruction(self):
        stale = Worker()
        with patch("app.worker.SessionLocal",self.sessions):
            self.assertFalse(await stale.checkpoint_history("task",[],["steer"]))
        async with self.sessions() as session:
            self.assertIsNone((await session.get(TaskControl,"steer")).processed_at)

    async def test_stop_control_is_visible_to_a_separate_worker(self):
        async with self.sessions() as session:
            session.add(TaskControl(task_id="task",kind="stop"))
            await session.commit()
        with patch("app.worker.SessionLocal",self.sessions):
            self.assertTrue(await Worker().stop_requested_in_db("task"))

    async def test_restart_recovers_queued_tasks(self):
        with patch("app.worker.SessionLocal",self.sessions):
            await self.worker.resume_interrupted_tasks()
        self.assertTrue(self.worker.is_active("queued"))


class DockerWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def test_workspace_name_cannot_target_an_arbitrary_container(self):
        for name in ("production-db","cockpit-../../secret","cockpit-not-a-task"):
            with self.assertRaises(ValueError):
                docker_sandbox.DockerSandbox(name)

    async def test_file_content_is_stdin_not_an_executable_shell_argument(self):
        response = SimpleNamespace(exit_code=0,stdout=b"",stderr=b"")
        execute = AsyncMock(return_value=response)
        files = docker_sandbox.DockerFiles("cockpit-"+"a"*32)
        with patch("app.docker_sandbox.docker_exec",execute):
            await files.write("/home/user/repo/test.txt","$(touch /unsafe)")
        self.assertEqual(execute.await_args.kwargs["input_data"],b"$(touch /unsafe)")
        self.assertNotIn("$(touch /unsafe)",execute.await_args.args)

    async def test_timeout_kills_the_workspace_before_releasing_command_ownership(self):
        sandbox = docker_sandbox.DockerSandbox("cockpit-"+"b"*32)
        sandbox.kill = AsyncMock()
        with patch("app.docker_sandbox.docker_exec",AsyncMock(side_effect=TimeoutError)):
            with self.assertRaises(RuntimeError):
                await sandbox.run("sleep 100",timeout=1)
        sandbox.kill.assert_awaited_once()
