"""Address format helpers: chain detection and Tron base58/hex conversion."""

import hashlib
import re

from app.models import Chain

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}

EVM_CHAINS = [
    Chain.ETHEREUM,
    Chain.BSC,
    Chain.POLYGON,
    Chain.ARBITRUM,
    Chain.OPTIMISM,
    Chain.BASE,
    Chain.AVALANCHE,
]

_TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BTC_RE = re.compile(r"^(bc1[02-9ac-hj-np-z]{11,71}|[13][1-9A-HJ-NP-Za-km-z]{25,34})$")
_SOLANA_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def b58encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    out = ""
    while num > 0:
        num, rem = divmod(num, 58)
        out = _B58_ALPHABET[rem] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    num = 0
    for char in text:
        num = num * 58 + _B58_INDEX[char]
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + body


def tron_hex_to_base58(hex_address: str) -> str:
    """Convert a Tron hex address (`41...`, or `0x...` 20-byte form) to base58check `T...`."""
    h = hex_address.lower()
    if h.startswith("0x"):
        h = "41" + h[2:]
    raw = bytes.fromhex(h)
    checksum = _sha256(_sha256(raw))[:4]
    return b58encode(raw + checksum)


def tron_base58_to_hex(address: str) -> str:
    raw = b58decode(address)
    return raw[:-4].hex()


def is_valid_tron_address(address: str) -> bool:
    if not _TRON_RE.match(address):
        return False
    raw = b58decode(address)
    if len(raw) != 25 or raw[0] != 0x41:
        return False
    return _sha256(_sha256(raw[:-4]))[:4] == raw[-4:]


def detect_chains(address: str) -> list[Chain]:
    """Return every chain an address could belong to, most likely first.

    An EVM address is valid on all EVM chains at once, so it maps to several.
    """
    address = address.strip()
    if is_valid_tron_address(address):
        return [Chain.TRON]
    if _EVM_RE.match(address):
        return list(EVM_CHAINS)
    if _BTC_RE.match(address):
        return [Chain.BITCOIN]
    if _SOLANA_RE.match(address):
        return [Chain.SOLANA]
    return []
