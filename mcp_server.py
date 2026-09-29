"""Start the ChainTrace MCP server (stdio):  python mcp_server.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
