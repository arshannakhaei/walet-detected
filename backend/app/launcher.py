"""Start the server the way a non-developer expects: find a usable port, say
clearly where the app is, and open the browser once it actually answers.

On Windows a port can be unusable even when nothing seems to listen on it:
Hyper-V, WSL and Docker reserve port ranges, and some programs hold a port
exclusively. Binding then fails with WinError 10013, and the browser may reach
whatever else owns the port and get an empty response. So the launcher test-binds
the port itself and moves on to the next one when that fails.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parents[2] / "chaintrace.log"
PORT_TRIES = 20

log = logging.getLogger("chaintrace.launcher")


def port_usable(host: str, port: int) -> bool:
    """True when this process could bind host:port (what uvicorn will do next)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            # Match uvicorn on Unix; on Windows SO_REUSEADDR would hide a taken port.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def browser_host(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


def chaintrace_running(host: str, port: int, timeout: float = 1.5) -> bool:
    """True when a ChainTrace server already answers on host:port."""
    url = f"http://{browser_host(host)}:{port}/api/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
    try:
        with opener.open(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ok" and "chains" in data
    except (OSError, ValueError):
        return False


def pick_port(host: str, preferred: int, tries: int = PORT_TRIES) -> int | None:
    for port in range(preferred, preferred + tries):
        if port_usable(host, port):
            return port
    return None


def open_when_ready(url: str, host: str, port: int, wait_seconds: float = 90) -> threading.Thread:
    def run():
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if chaintrace_running(host, port, timeout=1):
                webbrowser.open(url)
                return
            time.sleep(0.5)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _say(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:  # non-UTF-8 console or redirected output on Windows
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(message.encode(encoding, "replace").decode(encoding), flush=True)
    log.info(message)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(message)s",
    )
    open_browser = "--no-browser" not in argv

    if sys.version_info < (3, 11):
        _say(f"Python 3.11 or newer is required (this is {sys.version.split()[0]}).")
        _say("پایتون ۳.۱۱ یا جدیدتر لازم است: https://www.python.org/downloads/")
        return 1

    # A system proxy or VPN must never be used for talking to our own server.
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = ",".join(filter(None, [os.environ.get(key, ""), "localhost,127.0.0.1"]))

    try:
        import uvicorn

        from app.config import get_settings
    except ImportError as exc:
        _say(f"A required library is missing: {exc.name}. Run start.bat / start.sh again to install it.")
        _say("یک کتابخانه نصب نشده است؛ start.bat را دوباره اجرا کنید.")
        return 1

    settings = get_settings()
    host = settings.host

    if chaintrace_running(host, settings.port):
        url = f"http://{browser_host(host)}:{settings.port}"
        _say(f"ChainTrace is already running at {url}")
        _say(f"برنامه از قبل در حال اجراست: {url}")
        if open_browser:
            webbrowser.open(url)
        return 0

    port = pick_port(host, settings.port)
    if port is None:
        _say(f"No usable port between {settings.port} and {settings.port + PORT_TRIES - 1}.")
        _say("Set another port in .env, e.g. PORT=18765, and try again.")
        _say("هیچ پورت آزادی پیدا نشد؛ در فایل .env یک پورت دیگر بنویسید، مثلاً PORT=18765")
        return 1
    if port != settings.port:
        _say(f"Port {settings.port} is taken or blocked by Windows/another program; using port {port} instead.")
        _say(f"پورت {settings.port} در دسترس نبود؛ برنامه روی پورت {port} اجرا می‌شود.")

    url = f"http://{browser_host(host)}:{port}"
    _say("")
    _say(f"  ChainTrace  ->  {url}")
    _say(f"  API docs    ->  {url}/docs")
    _say("  (close this window to stop / برای توقف این پنجره را ببندید)")
    _say("")

    if open_browser:
        open_when_ready(url, host, port)
    uvicorn.run("app.main:app", host=host, port=port, app_dir=str(Path(__file__).resolve().parents[1]))
    return 0
