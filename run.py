"""Start ChainTrace with one command:  python run.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402

if __name__ == "__main__":
    settings = get_settings()
    print(f"ChainTrace running at http://{settings.host}:{settings.port}  (API docs: /docs)")
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, app_dir="backend")
