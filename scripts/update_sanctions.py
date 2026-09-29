"""Download OFAC-sanctioned crypto addresses into label files.

Source: the SDN list parsed by github.com/0xB10C/ofac-sanctioned-digital-currency-addresses.
Run from the project root:  python scripts/update_sanctions.py
"""

import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses/lists"
LABELS = Path(__file__).resolve().parent.parent / "backend" / "data" / "labels"

# list file code -> default chain. Token lists (USDT, USDC) mix chains, so
# every address is also routed by its format.
LISTS = {
    "XBT": "bitcoin",
    "ETH": "ethereum",
    "TRX": "tron",
    "USDT": "ethereum",
    "USDC": "ethereum",
    "BSC": "bsc",
    "ARB": "arbitrum",
    "SOL": "solana",
}


def route(address: str, default: str) -> tuple[str, str]:
    if address.startswith("0x") and len(address) == 42:
        return (default if default in ("bsc", "arbitrum") else "ethereum"), address.lower()
    if address.startswith("T") and len(address) == 34:
        return "tron", address
    if address.startswith("bc1") or (address[:1] in "13" and len(address) <= 35 and default != "solana"):
        return "bitcoin", address  # includes Omni-layer USDT
    return default, address


def fetch(code: str) -> list[str]:
    url = f"{BASE}/sanctioned_addresses_{code}.txt"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            text = resp.read().decode()
    except Exception as exc:  # noqa: BLE001 - report and continue with other lists
        print(f"  {code}: skipped ({exc})")
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def main() -> int:
    per_chain: dict[str, dict[str, dict]] = {}
    for code, default in LISTS.items():
        addresses = fetch(code)
        print(f"  {code}: {len(addresses)} addresses")
        for address in addresses:
            chain, key = route(address, default)
            per_chain.setdefault(chain, {})[key] = {
                "name": "OFAC sanctioned (SDN list)",
                "category": "sanctioned",
            }
    if not per_chain:
        print("nothing downloaded")
        return 1
    for chain, entries in per_chain.items():
        path = LABELS / f"{chain}.ofac.json"
        path.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {len(entries)} labels to {path.relative_to(LABELS.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
