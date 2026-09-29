from app.models import Chain
from app.services.addresses import (
    detect_chains,
    is_valid_tron_address,
    tron_base58_to_hex,
    tron_hex_to_base58,
)

USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_CONTRACT_HEX = "41a614f803b6fd780986a42c78ec9c7f77e6ded13c"


def test_tron_hex_base58_roundtrip():
    assert tron_hex_to_base58(USDT_CONTRACT_HEX) == USDT_CONTRACT
    assert tron_base58_to_hex(USDT_CONTRACT) == USDT_CONTRACT_HEX
    assert tron_hex_to_base58("0x" + USDT_CONTRACT_HEX[2:]) == USDT_CONTRACT


def test_tron_checksum_validation():
    assert is_valid_tron_address(USDT_CONTRACT)
    # Change one character: checksum must fail.
    assert not is_valid_tron_address(USDT_CONTRACT[:-1] + "u")
    assert not is_valid_tron_address("T123")


def test_detect_chains():
    assert detect_chains(USDT_CONTRACT) == [Chain.TRON]
    evm = detect_chains("0xdAC17F958D2ee523a2206206994597C13D831ec7")
    assert Chain.ETHEREUM in evm and Chain.BSC in evm
    assert detect_chains("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") == [Chain.BITCOIN]
    assert detect_chains("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") == [Chain.BITCOIN]
    assert detect_chains("So11111111111111111111111111111111111111112") == [Chain.SOLANA]
    assert detect_chains("not-an-address") == []


def test_builtin_label_files_are_valid():
    from app.services.labels import LabelCategory, load_builtin_labels

    labels = load_builtin_labels()
    assert labels, "no built-in labels found"
    for (chain, address), label in labels.items():
        assert chain in detect_chains(address), f"{address} is not a valid {chain.value} address"
        if chain in (Chain.ETHEREUM, Chain.BSC, Chain.ARBITRUM):
            assert address == address.lower()
    tornado = labels[(Chain.ETHEREUM, "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b")]
    assert tornado.category == LabelCategory.MIXER  # curated file beats the OFAC list
