"""The launcher: port selection and the "page opens with an empty response" case."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from app import launcher

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SilentServer:
    """Accepts connections and closes them without answering, like the program
    that held port 8000 on the user's PC (the browser shows ERR_EMPTY_RESPONSE)."""

    def __init__(self, port: int):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen()
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        self.sock.settimeout(0.2)
        while self.running:
            try:
                conn, _ = self.sock.accept()
                conn.close()
            except OSError:
                continue

    def close(self):
        self.running = False
        self.sock.close()


def test_port_usable_and_pick_port():
    port = _free_port()
    assert launcher.port_usable("127.0.0.1", port)
    blocker = SilentServer(port)
    try:
        assert not launcher.port_usable("127.0.0.1", port)
        assert not launcher.chaintrace_running("127.0.0.1", port)  # answers, but is not ChainTrace
        picked = launcher.pick_port("127.0.0.1", port)
        assert picked is not None and picked != port
    finally:
        blocker.close()


def test_pick_port_gives_up():
    port = _free_port()
    blocker = SilentServer(port)
    try:
        assert launcher.pick_port("127.0.0.1", port, tries=1) is None
    finally:
        blocker.close()


def test_browser_host():
    assert launcher.browser_host("0.0.0.0") == "127.0.0.1"
    assert launcher.browser_host("127.0.0.1") == "127.0.0.1"


def _health(port: int) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
            return json.loads(r.read())
    except OSError:
        return None


@pytest.fixture
def run_server(tmp_path):
    procs = []

    def start(port: int):
        env = {
            **os.environ,
            "PORT": str(port),
            "DEMO_MODE": "true",
            "MONITOR_INTERVAL_SECONDS": "0",
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / f'l{len(procs)}.db'}",
            "TELEGRAM_BOT_TOKEN": "",
        }
        proc = subprocess.Popen(
            [sys.executable, "run.py", "--no-browser"], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        procs.append(proc)
        return proc

    yield start
    for p in procs:
        p.terminate()
        p.wait(10)


def _wait_line(proc, needle: str, timeout: float = 40) -> str:
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        seen.append(line)
        if needle in line:
            return "".join(seen)
    pytest.fail(f"did not see {needle!r} in:\n{''.join(seen)}")


def test_blocked_port_moves_to_next_one(run_server):
    port = _free_port()
    blocker = SilentServer(port)
    try:
        proc = run_server(port)
        out = _wait_line(proc, "ChainTrace  ->")
        assert f"Port {port} is taken" in out
        new_port = int(out.split("ChainTrace  ->")[1].split(":")[-1].split()[0])
        assert new_port != port
        for _ in range(60):
            if _health(new_port):
                break
            time.sleep(0.5)
        assert _health(new_port)["status"] == "ok"
    finally:
        blocker.close()


def test_second_start_reuses_running_server(run_server):
    port = _free_port()
    first = run_server(port)
    _wait_line(first, "ChainTrace  ->")
    for _ in range(60):
        if _health(port):
            break
        time.sleep(0.5)
    second = run_server(port)
    out = second.communicate(timeout=30)[0]
    assert "already running" in out and second.returncode == 0
