import os
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            if candidate == here / ".env":
                break


_load_env()

import uvicorn  # noqa: E402

app = None
try:
    from app.main import app
except ImportError:
    app = None


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        app if app is not None else "app.main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
