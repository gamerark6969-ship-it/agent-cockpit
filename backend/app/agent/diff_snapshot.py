"""Keep reviewable code changes after an ephemeral cloud workspace is gone."""

import json

from sqlalchemy import delete

from ..db import SessionLocal
from ..models import Artifact, Task
from ..sandbox import REPO_DIR, _sh, mask_secrets


async def save_diff_snapshot(task_id: str,sandbox,base: str,owner: str) -> None:
    if sandbox is None:
        return
    try:
        prefix = f"cd {_sh(REPO_DIR)} && "
        ref = "origin/"+base
        result = await sandbox.commands.run(prefix+f"git diff --no-ext-diff {_sh(ref)} -- .",timeout=20)
        stat = await sandbox.commands.run(prefix+f"git diff --numstat {_sh(ref)} -- .",timeout=20)
        files = []
        for line in (stat.stdout or "").splitlines():
            parts = line.split("\t",2)
            if len(parts) == 3:
                added,deleted,path = parts
                files.append({"path":path,"status":"modified","additions":int(added) if added.isdigit() else 0,"deletions":int(deleted) if deleted.isdigit() else 0})
        patch = mask_secrets((result.stdout or "")[:512000])
        data = json.dumps({"summary":f"{len(files)} changed file(s)","files":files,"patch":patch}).encode()
        async with SessionLocal() as session:
            task = await session.get(Task,task_id)
            if task is None or task.lease_owner != owner:
                return
            await session.execute(delete(Artifact).where(Artifact.task_id == task_id,Artifact.kind == "diff"))
            session.add(Artifact(task_id=task_id,kind="diff",filename="changes.json",mime="application/json",size=len(data),data=data))
            await session.commit()
    except Exception:
        # Task completion does not depend on a best-effort diff capture.
        return
