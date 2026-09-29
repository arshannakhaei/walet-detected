"""Start ChainTrace with one command:  python run.py  [--no-browser]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.launcher import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
