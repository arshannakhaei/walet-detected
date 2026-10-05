"""Risk pattern detectors on hand-built transfer histories."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Chain, Transfer
from app.services.labels import Label, LabelCategory, LabelService
from app.services.risk import analyze_transfers, timeline, wallet_stats
from tests.conftest import USDT, make_address

W = make_address(100)
T0 = datetime(2024, 3, 1, tzinfo=timezone.utc)
FAKE_USDT = make_address(999)
_n = 0


def tx(frm, to, amount, minutes=0, contract=USDT, symbol="USDT") -> Transfer:
    global _n
    _n += 1
    return Transfer(
        chain=Chain.TRON,
        transfer_id=f"t{_n}",
        tx_hash=f"h{_n}",
        timestamp=T0 + timedelta(minutes=minutes),
        from_address=frm,
        to_address=to,
        amount=Decimal(amount),
        token_symbol=symbol,
        token_contract=contract,
        token_decimals=6,
    )


class _NoDb:
    async def list_labels(self):
        return []


def labels(**named: tuple[str, LabelCategory]) -> LabelService:
    builtin = {
        (Chain.TRON, addr): Label(chain=Chain.TRON, address=addr, name=name, category=cat, source="builtin")
        for addr, (name, cat) in named.items()
    }
    return LabelService(_NoDb(), builtin=builtin)


def codes(findings) -> set[str]:
    return {f.code for f in findings}


def test_clean_wallet_has_no_findings():
    history = [tx(make_address(1), W, 500, 0), tx(W, make_address(2), 120, 60 * 24 * 10)]
    assert analyze_transfers(Chain.TRON, W, history, labels()) == []


def test_pass_through_rapid_and_short_lived():
    a, b = make_address(1), make_address(2)
    history = [tx(a, W, 1000, 0), tx(W, b, 995, 20)]
    found = codes(analyze_transfers(Chain.TRON, W, history, labels()))
    assert {"pass_through", "rapid_movement", "short_lived_intermediary"} <= found
    stats = wallet_stats(W, history)
    assert stats.median_holding_hours == 20 / 60
    assert round(stats.pass_through_ratio, 3) == 0.995


def test_direct_exposure_to_sanctioned_address():
    bad = make_address(66)
    found = analyze_transfers(
        Chain.TRON, W, [tx(bad, W, 300)], labels(**{bad: ("OFAC", LabelCategory.SANCTIONED)})
    )
    exposure = next(f for f in found if f.code == "direct_exposure")
    assert exposure.points == 40 and bad in exposure.evidence[0]


def test_flagged_address_itself_scores_100():
    found = analyze_transfers(Chain.TRON, W, [], labels(**{W: ("Scam", LabelCategory.SCAM)}))
    assert found[0].code == "flagged_address" and found[0].points == 100


def test_fan_in_and_fan_out():
    history = [tx(make_address(200 + i), W, 50, i) for i in range(12)]
    history += [tx(W, make_address(300 + i), 40, 100 + i) for i in range(11)]
    found = codes(analyze_transfers(Chain.TRON, W, history, labels()))
    assert {"fan_in", "fan_out"} <= found


def test_round_amounts_and_structuring():
    history = [tx(make_address(1), W, 100_000, 0)]
    history += [tx(W, make_address(10 + i), 9500, 60 * 24 * i) for i in range(4)]
    history += [tx(W, make_address(20 + i), 700, 60 * 24 * (5 + i)) for i in range(2)]
    found = codes(analyze_transfers(Chain.TRON, W, history, labels()))
    assert {"round_amounts", "structuring", "new_high_volume"} <= found


def test_address_poisoning_victim():
    friend = "TXYZ" + "a" * 26 + "WXYZ"
    impostor = "TXYZ" + "b" * 26 + "WXYZ"
    history = [
        tx(W, friend, 100, 0),  # genuine payment
        tx(impostor, W, 0, 5, contract=FAKE_USDT),  # zero-value dust from the look-alike
        tx(W, impostor, 5000, 60),  # victim copies the wrong address
    ]
    found = {f.code: f for f in analyze_transfers(Chain.TRON, W, history, labels())}
    assert "poisoning_victim" in found
    assert impostor in found["poisoning_victim"].evidence[0]


def test_dormant_reactivated():
    history = [tx(make_address(1), W, 10, 0), tx(W, make_address(2), 5, 60 * 24 * 200)]
    assert "dormant_reactivated" in codes(analyze_transfers(Chain.TRON, W, history, labels()))


def test_spam_ignored_in_stats():
    history = [tx(make_address(1), W, 10, 0), tx(make_address(3), W, 99999, 1, contract=FAKE_USDT)]
    assert wallet_stats(W, history).distinct_senders == 1


def test_timeline_buckets():
    history = [tx(make_address(1), W, 10, 0), tx(W, make_address(2), 4, 60), tx(make_address(1), W, 1, 60 * 24 * 40)]
    days = timeline(W, history, "day")
    assert [(r["period"], r["amount_in"], r["amount_out"]) for r in days] == [
        ("2024-03-01", 10, 4),
        ("2024-04-10", 1, 0),
    ]
    assert [r["period"] for r in timeline(W, history, "month")] == ["2024-03", "2024-04"]


def test_truncated_history_is_not_called_new_and_services_are_recognised():
    from app.services.risk import SERVICE_COUNTERPARTIES

    start = datetime(2026, 10, 5, tzinfo=timezone.utc)
    payouts = [
        Transfer(
            chain=Chain.TRON, transfer_id=f"p{i}", tx_hash=f"p{i}", timestamp=start + timedelta(minutes=i),
            from_address=W, to_address=make_address(9000 + i), amount=Decimal(9500),
            token_symbol="USDT", token_contract=USDT, token_decimals=6,
        )
        for i in range(SERVICE_COUNTERPARTIES + 10)
    ]
    found = {f.code: f for f in analyze_transfers(Chain.TRON, W, payouts, labels(), truncated=True)}
    assert "new_high_volume" not in found  # only the newest day of a long history is known
    assert "likely_service" in found
    assert found["fan_out"].points == 0 and found["structuring"].points == 0
