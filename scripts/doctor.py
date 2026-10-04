"""ChainTrace troubleshooting report. Run:  python scripts/doctor.py  (or doctor.bat)

Prints what usually explains "the page does not open": Python version,
installed libraries, the configured port and whether it can be used, who
already answers on it, and proxy settings. Copy the output when asking for help.
API keys from .env are never printed.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.launcher import chaintrace_running, pick_port, port_usable  # noqa: E402

LIBS = ["fastapi", "uvicorn", "httpx", "pydantic", "pydantic_settings", "sqlalchemy", "aiosqlite", "greenlet", "aiogram", "matplotlib", "mcp"]


def line(label: str, value: object, ok: bool | None = None) -> None:
    mark = "" if ok is None else ("[OK]   " if ok else "[FAIL] ")
    print(f"{mark}{label}: {value}")


def env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, _, val = raw.partition("=")
                values[key.strip().upper()] = val.strip()
    return values


def windows_port_owner(port: int) -> str:
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return "netstat unavailable"
    rows = [r.split() for r in out.splitlines() if f":{port} " in r and "LISTEN" in r.upper()]
    if not rows:
        return "no listener (the port may be in a Windows reserved range: netsh interface ipv4 show excludedportrange protocol=tcp)"
    pids = sorted({r[-1] for r in rows})
    names = []
    for pid in pids:
        try:
            info = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout
            image = info.split(",")[0].strip().strip('"')
            names.append(f"{image} (PID {pid})" if image else f"PID {pid}")
        except (OSError, subprocess.SubprocessError):
            names.append(f"PID {pid}")
    return ", ".join(names)


def main() -> int:
    print("=== ChainTrace doctor ===")
    line("OS", f"{platform.system()} {platform.release()}")
    ok_py = sys.version_info >= (3, 11)
    line("Python", sys.version.split()[0], ok_py)
    line("Running from", sys.executable)

    missing = [m for m in LIBS if importlib.util.find_spec(m) is None]
    line("Libraries", "all installed" if not missing else f"missing: {', '.join(missing)}", not missing)

    env = env_values()
    line(".env", "found" if env else "not found (defaults are used)")
    host = env.get("HOST") or "127.0.0.1"
    port = int(env.get("PORT") or 8765)
    line("Configured address", f"{host}:{port}")
    line("Demo mode", env.get("DEMO_MODE", "false"))
    for key in ("TRONGRID_API_KEY", "ETHERSCAN_API_KEY", "TELEGRAM_BOT_TOKEN"):
        line(key, "set" if env.get(key) else "not set")
    print("       To test whether the keys actually work: check_keys.bat (python scripts/check_keys.py)")

    running = chaintrace_running(host, port)
    usable = port_usable(host, port)
    if running:
        line(f"Port {port}", "ChainTrace is already running here -> open it in the browser", True)
    elif usable:
        line(f"Port {port}", "free", True)
    else:
        owner = windows_port_owner(port) if os.name == "nt" else "in use"
        line(f"Port {port}", f"cannot be used ({owner})", False)
        alt = pick_port(host, port + 1)
        line("Next usable port", alt if alt else "none found", alt is not None)
        print("       run.py switches to that port automatically.")

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        val = os.environ.get(key) or os.environ.get(key.lower())
        if val:
            line(key, val)
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
                enabled = winreg.QueryValueEx(k, "ProxyEnable")[0]
                server = winreg.QueryValueEx(k, "ProxyServer")[0] if enabled else ""
            line("Windows system proxy", f"ON ({server}) - a VPN may intercept localhost; turn it off if the page is empty" if enabled else "off")
        except OSError:
            pass

    log = ROOT / "chaintrace.log"
    if log.exists():
        print("\n--- last lines of chaintrace.log ---")
        print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]))
    return 0 if ok_py and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
