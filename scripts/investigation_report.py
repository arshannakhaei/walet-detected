"""Build an investigation report for a few key wallets inside a list. Run:

    python scripts/investigation_report.py --focus ADDR ADDR ADDR --list wallets.txt --out reports/2026-10-06

`--list` takes a text file (one address per line) or the addresses themselves.
Writes report.html, graphs/ (PNG + SVG), data/ (CSV + JSON) and a ZIP of the folder.
API keys come from .env, as for the dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def read_addresses(values: list[str]) -> list[str]:
    from app.services.links import split_addresses

    out: list[str] = []
    for value in values:
        path = Path(value)
        out.extend(split_addresses(path.read_text(encoding="utf-8")) if path.is_file() else split_addresses(value))
    return out


async def run(args: argparse.Namespace) -> Path:
    from app.config import get_settings
    from app.container import create_services
    from app.services.investigation_report import write_report
    from app.services.addresses import resolve_address
    from app.services.links import resolve_members

    services = await create_services(get_settings())
    try:
        chain, members = resolve_members(read_addresses(args.list), None, services.providers.supported_chains)
        focus = [resolve_address(a, chain, services.providers.supported_chains)[1] for a in read_addresses(args.focus)]

        def progress(stage: str, done: int, total: int) -> None:
            print(f"  {stage}: {done}/{total}", flush=True)

        inv = await services.investigator.run(
            chain, focus, members, token=args.token, focus_limit=args.focus_limit, member_limit=args.member_limit,
            progress=progress,
        )
    finally:
        await services.close()
    out = Path(args.out or ROOT / "reports" / date.today().isoformat())
    archive = write_report(inv, out, title=args.title, case_date=args.case_date)
    for line in inv.warnings:
        print(f"  note: {line}")
    for p in inv.focus:
        v = p.verification
        print(f"  verification #{p.index}: {v.status if v else 'none'}")
    print(f"Report: {out / 'report.html'}\nZIP:    {archive}")
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--focus", nargs="+", required=True, help="the key wallets (addresses)")
    parser.add_argument("--list", nargs="+", required=True, help="the full list: a text file or addresses")
    parser.add_argument("--out", help="output folder (default: reports/<today>)")
    parser.add_argument("--token", default="USDT")
    parser.add_argument("--title", default=None, help="report title (Persian or English)")
    parser.add_argument("--case-date", default=None, help="date of the case, shown on the cover")
    parser.add_argument("--focus-limit", type=int, default=20_000, help="transfers downloaded per key wallet")
    parser.add_argument("--member-limit", type=int, default=5_000, help="transfers downloaded per other wallet")
    args = parser.parse_args(argv)
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
