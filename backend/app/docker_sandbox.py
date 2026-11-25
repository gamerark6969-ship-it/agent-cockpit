"""Per-task Docker workspaces on the free cloud runner; no API secrets mounted."""

import asyncio
import os
import re
from pathlib import Path
from types import SimpleNamespace

from .config import settings


async def docker_exec(*args: str, input_data: bytes | None = None, timeout: int = 300, extra_env: dict | None = None):
    env = {key:value for key,value in os.environ.items() if key.upper() in ("PATH","SYSTEMROOT","WINDIR","HOME","USERPROFILE")}
    env.update(extra_env or {})
    process = await asyncio.create_subprocess_exec("docker",*args,env=env,
        stdin=asyncio.subprocess.PIPE if input_data is not None else None,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        stdout,stderr = await asyncio.wait_for(process.communicate(input_data),timeout)
        if len(stdout)+len(stderr) > 16*1024*1024:
            raise RuntimeError("Sandbox output exceeded its limit")
        return SimpleNamespace(exit_code=process.returncode,stdout=stdout,stderr=stderr)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class DockerFiles:
    def __init__(self,name: str):
        self.name = name

    async def read(self,path: str,format: str = "text"):
        result = await docker_exec("exec",self.name,"python","-c",
            "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",path)
        if result.exit_code:
            raise RuntimeError("Sandbox file could not be read")
        return result.stdout if format == "bytes" else result.stdout.decode("utf-8",errors="replace")

    async def write(self,path: str,content):
        data = content.encode() if isinstance(content,str) else bytes(content)
        result = await docker_exec("exec","-i",self.name,"python","-c",
            "import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read())",
            path,input_data=data)
        if result.exit_code:
            raise RuntimeError("Sandbox file could not be written")


class DockerSandbox:
    def __init__(self,name: str):
        if not re.fullmatch(r"cockpit-[a-f0-9]{32}",name):
            raise ValueError("Invalid sandbox identifier")
        self.name = name
        self.sandbox_id = "docker:"+name
        self.commands = self
        self.files = DockerFiles(name)

    async def run(self,command: str,timeout: int = 300):
        try:
            result = await docker_exec("exec",self.name,"sh","-lc",command,timeout=timeout)
        except TimeoutError:
            await self.kill()
            raise RuntimeError("Sandbox command timed out; its container was stopped") from None
        return SimpleNamespace(exit_code=result.exit_code,stdout=result.stdout.decode(errors="replace"),stderr=result.stderr.decode(errors="replace"))

    async def set_timeout(self,seconds: int):
        return None  # The bounded worker owns this container's lifetime.

    def get_host(self,port: int):
        raise RuntimeError("Use the persistent artifact or GitHub Pages preview")

    async def kill(self):
        await docker_exec("rm","-f",self.name,timeout=30)


async def create_sandbox(task_id: str,envs: dict) -> DockerSandbox:
    clean = task_id.replace("-","")
    if not re.fullmatch(r"[a-f0-9]{32}",clean):
        raise ValueError("Invalid task identifier")
    sandbox = DockerSandbox("cockpit-"+clean)
    root = Path(settings.DOCKER_SANDBOX_DIR).resolve()/clean
    root.mkdir(parents=True,exist_ok=True)
    args = ["run","--detach","--name",sandbox.name,"--memory","2g","--cpus","2",
            "--pids-limit","256","--security-opt","no-new-privileges","-v",f"{root}:/home/user"]
    for key in envs:
        args.extend(("--env",key))
    result = await docker_exec(*args,settings.DOCKER_SANDBOX_IMAGE,extra_env=envs,timeout=120)
    if result.exit_code:
        raise RuntimeError("Docker sandbox could not start; build its image on the cloud runner")
    return sandbox


async def connect_sandbox(sandbox_id: str) -> DockerSandbox:
    sandbox = DockerSandbox(sandbox_id.removeprefix("docker:"))
    result = await docker_exec("inspect","--format","{{.State.Running}}",sandbox.name,timeout=20)
    if result.exit_code or result.stdout.strip() != b"true":
        raise RuntimeError("Cloud workspace has expired")
    return sandbox
