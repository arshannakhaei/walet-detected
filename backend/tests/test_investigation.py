"""Investigation report: collected numbers, verification, graph files and the HTML page."""

import asyncio
import csv
import io
import json
import shutil
import struct
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import Chain, TokenBalance, Transfer
from app.services.fx import TomanRate, Value
from app.services.investigation import Investigator, member_matrix, monthly_series, short
from app.services.investigation_graphs import DPI, fmt_amount, fmt_compact, render_all, short_tag
from app.services.investigation_report import num, render_html, summary_sentences, write_report, _Ctx
from app.services.jalali import jalali, jalali_month, to_jalali
from app.services.labels import Label, LabelCategory
from app.services.tronscan import AccountInfo, ScanTransfer, compare, parse_transfers, tag_category
from tests.conftest import USDT, make_address

T0 = datetime(2022, 9, 15, 7, 0, tzinfo=timezone.utc)
A, B, C, D, E = (make_address(n) for n in (501, 502, 503, 504, 505))  # the list; A, B, C are the key wallets
MID = make_address(601)  # outside wallet both A and C dealt with
EXCHANGE = make_address(602)
SRC = make_address(603)
MEMBERS = [D, A, B, E, C]  # list order: D=#1, A=#2, B=#3, E=#4, C=#5


def tr(n: int, frm: str, to: str, amount, days: float, symbol="USDT", contract=USDT, success=True) -> Transfer:
    return Transfer(
        chain=Chain.TRON,
        transfer_id=f"t{n}",
        tx_hash=f"{n:064x}",
        timestamp=T0 + timedelta(days=days),
        from_address=frm,
        to_address=to,
        amount=Decimal(str(amount)),
        token_symbol=symbol,
        token_contract=contract,
        token_decimals=6,
        success=success,
    )


TRANSFERS = [
    tr(1, SRC, A, 1000, 0),
    tr(2, A, B, 300, 1),  # key -> key
    tr(3, A, B, 200.5, 40),  # key -> key, next month
    tr(4, B, EXCHANGE, 450, 41),
    tr(5, A, MID, 400, 2),  # A and C share MID
    tr(6, MID, C, 390, 2.1),
    tr(7, D, A, 50, 3),  # another list member pays a key wallet
    tr(8, C, E, 120, 5),
    tr(9, A, B, 7, 6, symbol="TRX", contract=None),  # native coin between key wallets
    tr(10, SRC, A, 999, 7, symbol="USDT", contract=make_address(999)),  # fake USDT: spam
    tr(11, SRC, A, 0, 8),  # zero value: spam
    tr(12, A, B, 55, 9, success=False),  # failed
    tr(13, E, D, 30, 10),  # between two non-key members
]


class FakeWallets:
    def __init__(self, transfers=TRANSFERS, truncated=()):
        self.transfers = transfers
        self.truncated = set(truncated)
        self.limits: dict[str, int | None] = {}

    async def load_transfers(self, chain, address, refresh=False, limit=None, quick=False):
        self.limits[address] = limit
        own = [t for t in self.transfers if address in (t.from_address, t.to_address)]
        return sorted(own, key=lambda t: t.timestamp, reverse=True), address in self.truncated


class FakeProvider:
    async def get_balances(self, address):
        balances = [TokenBalance(token_symbol="TRX", amount=Decimal("1.5"))]
        if address == A:
            balances.append(TokenBalance(token_symbol="USDT", token_contract=USDT, amount=Decimal("149.5")))
        return balances


class FakeProviders:
    def get(self, chain):
        return FakeProvider()


class FakeLabels:
    def get(self, chain, address):
        if address == EXCHANGE:
            return Label(chain=chain, address=address, name="Exchange X", category=LabelCategory.EXCHANGE, source="builtin")
        return None


class FakeValues:
    """USDT is one dollar, TRX ten cents; one dollar is 50,000 toman (None when `toman` is off)."""

    def __init__(self, toman=True):
        self.toman = toman

    async def toman_now(self):
        return TomanRate(rate=Decimal(50000) if self.toman else None, source="manual" if self.toman else None, updated_at=None)

    async def values(self, items):
        out = []
        for _, symbol, contract, amount, when in items:
            price = Decimal(1) if contract == USDT else (Decimal("0.1") if contract is None else None)
            usd = amount * price if price is not None else None
            toman = usd * 50000 if usd is not None and self.toman else None
            out.append(Value(usd_then=usd if when else None, usd_now=usd, toman_then=toman if when else None, toman_now=toman))
        return out


class FakeTronScan:
    def __init__(self, wallets: FakeWallets, drop: str | None = None):
        self.wallets = wallets
        self.drop = drop  # tx hash TronScan "does not know"

    async def account(self, address):
        return AccountInfo(address=address, tag="Binance-Hot 9" if address == SRC else None, is_contract=address == B)

    async def verify(self, address, symbol, contract, ours):
        scan = [
            ScanTransfer(tx_hash=t.tx_hash, timestamp=t.timestamp, from_address=t.from_address, to_address=t.to_address, amount=t.amount)
            for t in self.wallets.transfers
            if address in (t.from_address, t.to_address) and t.token_contract == contract and t.success and t.amount > 0
            and t.tx_hash != self.drop
        ]  # fmt: skip
        return compare(address, symbol, ours, scan)


def investigate(wallets=None, values=None, tronscan="default", focus=(A, B, C), members=MEMBERS):
    wallets = wallets or FakeWallets()
    scan = FakeTronScan(wallets) if tronscan == "default" else tronscan
    investigator = Investigator(wallets, FakeProviders(), FakeLabels(), values or FakeValues(), tronscan=scan)
    return asyncio.run(investigator.run(Chain.TRON, list(focus), list(members), focus_limit=20_000, member_limit=700))


@pytest.fixture(scope="module")
def inv():
    return investigate()


# --- collected numbers ------------------------------------------------------------------


def test_focus_wallets_keep_list_numbers_and_get_the_full_history_limit():
    wallets = FakeWallets()
    result = investigate(wallets)
    assert [(p.index, p.address) for p in result.focus] == [(2, A), (3, B), (5, C)]
    assert wallets.limits[A] == 20_000 and wallets.limits[D] == 700


def test_profile_totals_ignore_spam_and_failed_transfers(inv):
    a = inv.focus[0]
    usdt = next(t for t in a.totals if t.token_contract == USDT)
    assert (usdt.total_in, usdt.count_in) == (Decimal("1050"), 2)
    assert (usdt.total_out, usdt.count_out) == (Decimal("900.5"), 3)
    assert usdt.usd_in_then == Decimal("1050") and usdt.toman_out_then == Decimal("900.5") * 50000
    trx = next(t for t in a.totals if t.token_contract is None)
    assert trx.total_out == Decimal("7") and trx.usd_out_then == Decimal("0.7")
    assert a.spam_count == 2  # the fake USDT and the zero-value transfer
    assert a.transfer_count == 6  # 5 USDT + 1 TRX; the failed one is not counted
    assert a.counterparty_count == 4  # SRC, B, MID, D
    assert [b.amount for b in a.balances if b.token_contract == USDT] == [Decimal("149.5")]
    assert a.first_seen == T0 and a.last_seen == T0 + timedelta(days=40)


def test_top_parties_and_largest_transfers(inv):
    a = inv.focus[0]
    assert [(p.address, p.amount, p.count, p.index) for p in a.top_senders] == [
        (SRC, Decimal("1000"), 1, None),
        (D, Decimal("50"), 1, 1),
    ]
    assert [(p.address, p.amount, p.count) for p in a.top_receivers] == [(B, Decimal("500.5"), 2), (MID, Decimal("400"), 1)]
    assert a.top_senders[0].label == "Binance-Hot 9" and a.top_senders[0].category == "exchange"  # TronScan tag
    assert [t.amount for t in a.largest][:2] == [Decimal("1000"), Decimal("400")]
    assert inv.focus[1].is_contract is True and inv.focus[0].is_contract is False


def test_transfers_between_focus_wallets_are_chronological_with_values(inv):
    rows = [(t.from_index, t.to_index, t.amount, t.token_symbol) for t in inv.focus_transfers]
    assert rows == [(2, 3, Decimal("300"), "USDT"), (2, 3, Decimal("7"), "TRX"), (2, 3, Decimal("200.5"), "USDT")]
    assert inv.focus_transfers[0].usd_then == Decimal("300") and inv.focus_transfers[1].usd_then == Decimal("0.7")
    assert inv.focus_transfers[0].toman_then == Decimal("15000000")


def test_matrix_ranking_and_member_transfers(inv):
    i = {a: n for n, a in enumerate(MEMBERS)}
    assert inv.matrix[i[A]][i[B]] == Decimal("500.5") and inv.matrix_counts[i[A]][i[B]] == 2
    assert inv.matrix[i[D]][i[A]] == Decimal("50") and inv.matrix[i[C]][i[E]] == Decimal("120")
    assert inv.matrix[i[E]][i[D]] == Decimal("30")
    assert sum(sum(row, Decimal(0)) for row in inv.matrix) == Decimal("700.5")  # TRX and spam are not in it
    assert len(inv.member_transfers) == 6  # 5 USDT + the TRX one, each once
    assert [r.index for r in inv.ranking][:2] == [2, 3]  # A (550.5 with members), then B (500.5)
    a = inv.ranking[0]
    assert (a.sent_to_members, a.received_from_members, a.partners, a.transfers_with_members) == (
        Decimal("500.5"), Decimal("50"), 2, 3,
    )  # fmt: skip
    assert [m.index for m in inv.most_connected] == [4, 1]  # E (150), D (80): the non-focus members with flows
    e = inv.most_connected[0]
    assert [(p.index, p.sent, p.received) for p in e.partners] == [(5, Decimal(0), Decimal("120")), (1, Decimal("30"), Decimal(0))]


def test_member_matrix_counts_only_the_token():
    amounts, counts = member_matrix([A, B], TRANSFERS, USDT, "USDT")
    assert amounts == [[Decimal(0), Decimal("555.5")], [Decimal(0), Decimal(0)]]  # raw list: the failed one included
    amounts, _ = member_matrix([A, B], TRANSFERS, None, "TRX")
    assert amounts[0][1] == Decimal("7")
    assert counts[0][1] == 3


def test_links_find_the_shared_outside_wallet(inv):
    paths = [(p.from_address, p.via, p.to_address) for p in inv.links_all.paths]
    assert (A, [MID], C) in paths
    matched = next(p for p in inv.links_all.paths if p.via == [MID])
    assert matched.matched_amount == Decimal("390")
    assert inv.labels[SRC] == "Binance-Hot 9" and inv.label_sources[SRC] == "tronscan"
    assert inv.labels[EXCHANGE] == "Exchange X" and inv.categories[EXCHANGE] == "exchange"


def test_monthly_series_fills_empty_months_and_keeps_a_running_balance(inv):
    rows = inv.focus[0].monthly["USDT"]
    assert [r.period for r in rows] == ["2022-09", "2022-10"]
    assert (rows[0].amount_in, rows[0].amount_out, rows[0].balance) == (Decimal("1050"), Decimal("700"), Decimal("350"))
    assert (rows[1].amount_out, rows[1].balance) == (Decimal("200.5"), Decimal("149.5"))
    gap = monthly_series(A, [tr(1, SRC, A, 10, 0), tr(2, A, B, 4, 100)], USDT)
    assert [r.period for r in gap] == ["2022-09", "2022-10", "2022-11", "2022-12"]
    assert [r.balance for r in gap] == [Decimal(10), Decimal(10), Decimal(10), Decimal(6)]
    assert inv.focus[0].monthly["TRX"][0].amount_out == Decimal("7")


def test_truncated_and_missing_toman_are_reported():
    result = investigate(FakeWallets(truncated={D}), values=FakeValues(toman=False))
    assert any("#1" in w and "700" in w for w in result.warnings)
    assert any("toman" in w for w in result.warnings)
    assert result.toman.rate is None and result.focus_transfers[0].toman_then is None
    assert next(m for m in result.members if m.address == D).truncated


# --- verification ---------------------------------------------------------------------------


def test_verification_is_green_when_tronscan_agrees(inv):
    for p in inv.focus:
        assert p.verification.status == "verified"
    v = inv.focus[0].verification
    assert (v.our_count, v.scan_count, v.our_in, v.scan_in, v.our_out, v.scan_out) == (
        5, 5, Decimal("1050"), Decimal("1050"), Decimal("900.5"), Decimal("900.5"),
    )  # fmt: skip


def test_verification_reports_a_mismatch_with_the_transaction():
    wallets = FakeWallets()
    result = investigate(wallets, tronscan=FakeTronScan(wallets, drop=f"{2:064x}"))
    v = result.focus[0].verification
    assert v.status == "mismatch" and v.only_ours == [f"{2:064x}"] and v.only_scan == []
    assert (v.our_count, v.scan_count) == (5, 4) and v.scan_out == Decimal("600.5")
    assert result.focus[2].verification.status == "verified"  # C does not have that transfer


def test_verification_unavailable_without_tronscan():
    result = investigate(tronscan=None)
    assert {p.verification.status for p in result.focus} == {"unavailable"}


def test_compare_treats_repeated_transfers_in_one_transaction_as_a_multiset():
    ours = [tr(1, A, B, 5, 0), tr(1, A, B, 5, 0)]
    one = ScanTransfer(tx_hash=ours[0].tx_hash, timestamp=T0, from_address=A, to_address=B, amount=Decimal(5))
    assert compare(A, "USDT", ours, [one, one]).status == "verified"
    assert compare(A, "USDT", ours, [one]).status == "mismatch"
    zero = one.model_copy(update={"amount": Decimal(0), "tx_hash": "ff"})
    assert compare(A, "USDT", ours, [one, one, zero]).status == "verified"  # zero-value entries are ignored


def test_tronscan_parsing_and_tags():
    items = [
        {"transaction_id": "aa", "block_ts": 1717128585000, "from_address": A, "to_address": B, "quant": "239312198",
         "contractRet": "SUCCESS", "tokenInfo": {"tokenDecimal": 6}},
        {"transaction_id": "bb", "block_ts": 1717128585000, "from_address": A, "to_address": B, "quant": "5",
         "contractRet": "REVERT", "tokenInfo": {"tokenDecimal": 6}},
    ]  # fmt: skip
    parsed = parse_transfers(items)
    assert [(t.tx_hash, t.amount) for t in parsed] == [("aa", Decimal("239.312198"))]
    assert parsed[0].timestamp == datetime(2024, 5, 31, 4, 9, 45, tzinfo=timezone.utc)
    assert tag_category("Binance-Hot 2") == "exchange" and tag_category("Okex 1") == "exchange"
    assert tag_category("Some Foundation") == "service"


# --- formatting --------------------------------------------------------------------------------


def test_jalali_dates():
    assert to_jalali(date(2022, 9, 15)) == (1401, 6, 24)
    assert to_jalali(date(2024, 3, 20)) == (1403, 1, 1)  # Nowruz
    assert to_jalali(date(2024, 3, 19)) == (1402, 12, 29)
    assert to_jalali(date(2021, 3, 20)) == (1399, 12, 30)  # leap year
    assert jalali(date(2022, 9, 15)) == "۲۴ شهریور ۱۴۰۱"
    assert jalali(date(2022, 9, 15), month_name=False) == "۱۴۰۱/۰۶/۲۴"
    assert jalali_month("2022-09") == "شهریور ۱۴۰۱"


def test_number_and_name_formats():
    assert num(Decimal("10304.349825")) == "۱۰٬۳۰۴٫۳۵" and num(Decimal("500")) == "۵۰۰" and num(None) == "—"
    assert fmt_amount(Decimal("10304.35"), "USDT") == "10,304 USDT" and fmt_amount(Decimal("11.17")) == "11.17"
    assert fmt_compact(10500) == "10.5K" and fmt_compact(2_000_000) == "2M" and fmt_compact(330) == "330"
    assert short(A, 4) == f"#4 {A[:5]}...{A[-4:]}"
    assert short_tag("Exchange hot wallet (likely Binance)") == "Binance (likely)"


# --- graphs, page and files ----------------------------------------------------------------------


def png_info(path: Path) -> tuple[int, int, float]:
    """(width, height, dots per inch) from the PNG header and its pHYs chunk."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    at = data.index(b"pHYs")
    per_metre = struct.unpack(">I", data[at + 4 : at + 8])[0]
    return width, height, per_metre * 0.0254


@pytest.fixture(scope="module")
def report_dir(inv, tmp_path_factory):
    out = tmp_path_factory.mktemp("report") / "2026-10-06"
    archive = write_report(inv, out, case_date="۱۴۰۵/۰۷/۱۴", spot_checks=["نمونهٔ دستی"])
    return out, archive


def test_graph_files_are_print_quality(report_dir, inv):
    out, _ = report_dir
    keys = ["focus_network", "joint_timeline", "list_network", "matrix_heatmap", "ranking_bar"]
    keys += [f"{kind}_{p.index}" for p in inv.focus for kind in ("flow", "timeline")]
    for key in keys:
        png, svg = out / "graphs" / f"{key}.png", out / "graphs" / f"{key}.svg"
        width, height, dpi = png_info(png)
        assert round(dpi) == DPI == 300, key
        assert width >= 2400 and height >= 1200, (key, width, height)
        assert png.stat().st_size > 30_000, key
        text = svg.read_text(encoding="utf-8")
        assert text.lstrip().startswith("<?xml") and "<svg" in text and len(text) > 5_000, key
    assert len(list((out / "graphs").iterdir())) == 2 * len(keys)


def test_render_all_returns_files_in_report_order(inv, tmp_path):
    files = render_all(inv, tmp_path)
    assert [f.key for f in files][:4] == ["focus_network", "joint_timeline", "flow_2", "timeline_2"]
    assert all(f.png.exists() and f.svg.exists() and f.width > 0 for f in files)


def test_html_has_every_section_and_no_outside_resources(report_dir, inv):
    out, _ = report_dir
    html = (out / "report.html").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and '<html lang="fa" dir="rtl">' in html
    for section in ("cover", "summary", "connections", "timeline", "wallet-2", "wallet-3", "wallet-5", "list", "method", "appendix"):
        assert f'id="{section}"' in html, section
    for words in ("خلاصهٔ مدیریتی", "ارتباط کیف‌های اصلی", "راستی‌آزمایی با TronScan", "محدودیت‌ها", "واژه‌نامه", "نمونهٔ دستی"):
        assert words in html, words
    for key in ("focus_network", "joint_timeline", "flow_2", "timeline_5", "list_network", "matrix_heatmap", "ranking_bar"):
        assert f'src="graphs/{key}.png"' in html and f'href="graphs/{key}.svg" download' in html
    assert "✓ مطابق" in html and "✗ مغایرت" not in html
    assert A in html and f"{2:064x}" in html  # full addresses and transaction links
    assert "۱۴۰۵/۰۷/۱۴" in html  # the case date
    # Works offline: the only links that leave the folder are TronScan's.
    import re

    outside = set(re.findall(r'(?:src|href)="(https?://[^"/]+)', html))
    assert outside <= {"https://tronscan.org"}
    assert "<link" not in html and "@import" not in html
    assert "تومان" in html  # the toman column is there when rates are known


def test_html_without_toman_drops_the_column_and_says_why(tmp_path):
    result = investigate(values=FakeValues(toman=False))
    graphs = render_all(result, tmp_path / "graphs")
    html = render_html(result, graphs, [])
    assert "ارزش روز (تومان)" not in html
    assert "نرخ تومان در زمان تهیهٔ گزارش در دسترس نبود" in html


def test_summary_states_the_strongest_facts(inv):
    text = " ".join(summary_sentences(_Ctx(inv)))
    assert "۲ انتقال" in text and "۵۰۰٫۵" in text  # A -> B: two transfers, 500.5 USDT
    assert "هیچ انتقال مستقیم" in text  # A-C and B-C have none
    assert short(MID) in text  # ... but A and C share an unlabelled wallet
    assert "حساب قرارداد هوشمند" in text  # B is a contract account
    assert "دقیقاً مطابقت" in text


def test_data_exports(report_dir, inv):
    out, archive = report_dir
    raw = (out / "data" / f"transfers_02_{A}.csv").read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf"  # BOM, so Excel shows Persian
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0][0] == "تاریخ (UTC)" and len(rows) == 1 + 6
    assert rows[1][2] == "ورودی" and rows[1][3] == SRC and rows[1][6] == "1000" and rows[1][5] == "Binance-Hot 9"
    matrix = list(csv.reader(io.StringIO((out / "data" / "matrix.csv").read_text(encoding="utf-8-sig"))))
    assert len(matrix) == 1 + len(MEMBERS) + 1 and matrix[2][3] == "500.5"  # row A (#2), column B (#3)
    ranking = list(csv.reader(io.StringIO((out / "data" / "ranking.csv").read_text(encoding="utf-8-sig"))))
    assert ranking[1][1] == "2" and ranking[1][8] == "550.5"
    between = list(csv.reader(io.StringIO((out / "data" / "transfers_between_members.csv").read_text(encoding="utf-8-sig"))))
    assert len(between) == 1 + 6
    data = json.loads((out / "data" / "investigation.json").read_text(encoding="utf-8"))
    assert data["token"] == "USDT" and len(data["members"]) == 5 and data["focus"][0]["verification"]["status"] == "verified"

    assert archive.name == "ChainTrace-Report-2026-10-06.zip"
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
    assert "report.html" in names and "graphs/focus_network.png" in names and "graphs/focus_network.svg" in names
    assert "data/matrix.csv" in names and f"data/transfers_02_{A}.csv" in names
    assert all(n == "report.html" or n.startswith(("graphs/", "data/")) for n in names)


def test_single_focus_wallet_and_empty_list_links_still_render(tmp_path):
    result = investigate(focus=(A,), members=[A, D])
    assert [p.index for p in result.focus] == [1] and result.focus_transfers == []
    archive = write_report(result, tmp_path / "one")
    assert archive.exists() and "هیچ انتقال مستقیمی بین کیف‌های اصلی دیده نشد" in (tmp_path / "one" / "report.html").read_text(encoding="utf-8")


def test_cli_reads_addresses_from_a_file_or_arguments(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("investigation_cli", Path(__file__).resolve().parents[2] / "scripts" / "investigation_report.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    listing = tmp_path / "wallets.txt"
    listing.write_text(f"{A}\n{B}, {C}\n", encoding="utf-8")
    assert cli.read_addresses([str(listing), D]) == [A, B, C, D]


# --- in a real browser -----------------------------------------------------------------------------

CHROME_CANDIDATES = [
    shutil.which("chromium") or "",
    shutil.which("google-chrome") or "",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
]


@pytest.mark.parametrize("width", [375, 1400])
def test_report_fits_the_screen_without_errors(report_dir, width):
    sync_api = pytest.importorskip("playwright.sync_api")
    chrome = next((p for p in CHROME_CANDIDATES if p and Path(p).exists()), None)
    if chrome is None:
        pytest.skip("no Chromium binary")
    out, _ = report_dir
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto((out / "report.html").as_uri())
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.scrollWidth") <= width  # no horizontal scroll
        assert page.evaluate("document.querySelectorAll('section').length") >= 9
        page.locator("figure img").first.scroll_into_view_if_needed()  # images load lazily
        page.wait_for_function("(() => { const i = document.querySelector('figure img'); return i.complete && i.naturalWidth > 0 })()")
        assert page.evaluate("getComputedStyle(document.body).direction") == "rtl"
        page.click("#theme")
        assert page.evaluate("document.documentElement.dataset.theme") in ("dark", "light")
        browser.close()
    assert errors == []
