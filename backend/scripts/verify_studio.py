"""Check real application routes and signed MP4 seeking without starting workers."""

import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0,str(ROOT))

from app.config import settings
from app.automation.config import automation_settings

settings.BACKGROUND_WORKER_ENABLED = False
automation_settings.enabled = False

from app.main import app


if __name__ == "__main__":
    with TestClient(app) as client:
        headers = {"Authorization":f"Bearer {settings.APP_TOKEN}"}
        status = client.get("/api/automation/status",headers=headers)
        status.raise_for_status()
        print("Studio status:",status.status_code,"models:",len(status.json()["models"]))
        runs = client.get("/api/automation/runs",headers=headers)
        runs.raise_for_status()
        draft = next((run for run in runs.json() if run.get("media_url")),None)
        if not draft:
            print("No rendered draft yet. Run smoke_video.py --save-studio first.")
            raise SystemExit(1)
        detail = client.get(f"/api/automation/runs/{draft['id']}",headers=headers)
        detail.raise_for_status()
        print("Studio draft:",detail.json()["title"],detail.json()["status"])
        parts = urlsplit(detail.json()["media_url"])
        if not automation_settings.s3_configured:
            response = client.get(parts.path+"?"+parts.query,headers={"Range":"bytes=0-63"})
            if response.status_code != 206 or len(response.content) != 64:
                raise RuntimeError("Signed video seeking failed")
            print("Signed MP4 seeking:",response.status_code,len(response.content),"bytes")
        models = client.get("/api/models",headers=headers)
        models.raise_for_status()
        print("Configured model routes:",", ".join(models.json()["models"]))
