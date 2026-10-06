"""Registry of well-known token contracts, used to spot fake look-alike tokens.

Scam tokens often copy a real token's symbol (a fake "USDT") and are sent in
bulk to make wallets look funded or to poison address books. A transfer whose
symbol matches a known token but whose contract does not is treated as spam.
"""

import re
import unicodedata

from app.models import Chain, Transfer

# chain -> contract (canonical form) -> symbol
KNOWN_TOKENS: dict[Chain, dict[str, str]] = {
    Chain.TRON: {
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t": "USDT",
        "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8": "USDC",
    },
    Chain.ETHEREUM: {
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
        "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
    },
    Chain.BSC: {
        "0x55d398326f99059ff775485246999027b3197955": "USDT",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",
        "0xe9e7cea3dedca5984780bafc599bd69add087d56": "BUSD",
    },
    Chain.POLYGON: {
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC",
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.E",
    },
    Chain.ARBITRUM: {
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": "USDT",
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC",
    },
    Chain.OPTIMISM: {
        "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": "USDT",
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": "USDC",
    },
    Chain.BASE: {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
    },
    Chain.AVALANCHE: {
        "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": "USDT",
        "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": "USDC",
    },
    Chain.SOLANA: {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    },
}

# Symbols worth protecting on every chain, even where we list no contract.
_PROTECTED = {"USDT", "USDC", "DAI", "BUSD", "WETH", "WBTC", "ETH", "BTC", "TRX", "BNB", "SOL"}

STABLECOINS = {"USDT", "USDC", "USDC.E", "DAI", "BUSD", "TUSD", "USDD", "FDUSD"}


def known_symbol(chain: Chain, contract: str | None) -> str | None:
    if contract is None:
        return None
    return KNOWN_TOKENS.get(chain, {}).get(contract)


# Advert tokens airdropped to wallets: a website, Telegram handle or "buy ..." as the name.
_ADVERT = re.compile(
    r"https?:|www\.|t\.me|telegram|@|\bqq\b|\b(buy|claim|reward|airdrop|visit|gift|bonus|free)\b"
    r"|\.\s*(com|net|org|xyz|top|vip|fun|cc|club|cn|c0m)\b|\s(com|c0m)\s*$",
    re.IGNORECASE,
)


def _lookalike(symbol: str) -> bool:
    """Letters from other alphabets that look Latin (a Cyrillic "т" in "USDт")."""
    for ch in symbol:
        if ord(ch) > 127 and ch.isalpha():
            name = unicodedata.name(ch, "")
            if name.startswith(("CYRILLIC", "GREEK")) or "FULLWIDTH" in name or "MATHEMATICAL" in name:
                return True
    return False


def is_spam_token(chain: Chain, symbol: str, contract: str | None) -> bool:
    """A token that only exists to advertise or to impersonate a well-known one."""
    if contract is None or known_symbol(chain, contract) is not None:
        return False
    clean = symbol.strip()
    upper = clean.upper()
    if upper in _PROTECTED or upper in {s.upper() for s in KNOWN_TOKENS.get(chain, {}).values()}:
        return True  # a fake "USDT" with an unknown contract
    return bool(_ADVERT.search(clean)) or _lookalike(clean)


def is_spam(t: Transfer) -> bool:
    """Zero-value transfers, advert tokens and look-alike tokens (known symbol, unknown contract)."""
    if t.amount == 0:
        return True
    return is_spam_token(t.chain, t.token_symbol, t.token_contract)
