"""Investigation report: one self-contained Persian HTML page, graph images and data exports.

`write_report` fills a folder:

    report.html              the report (works offline; open in any browser, print to PDF)
    graphs/*.png, *.svg      every graph, for pasting into another document
    data/*.csv, *.json       the numbers behind the report (CSV opens in Excel)

and zips it. The page is right-to-left, readable on a phone, has light and dark
colours and prints on A4.
"""

import base64
import csv
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from pathlib import Path

from app.bot.texts import FINDING_FA
from app.config import PROJECT_ROOT
from app.services.investigation import FocusProfile, Investigation, MemberProfile, TransferRec, is_service, short
from app.services.investigation_graphs import FOCUS_COLORS, GraphFile, focus_edges, render_all, short_tag
from app.services.jalali import fa_digits, jalali
from app.services.tronscan import ADDRESS_URL, TX_URL

DEFAULT_TITLE = "گزارش تحلیل ارتباط کیف‌پول‌ها"
FONT_DIR = PROJECT_ROOT / "frontend" / "dist" / "assets"
LEVEL_FA = {"low": "کم", "medium": "متوسط", "high": "زیاد", "critical": "بحرانی"}
SEVERITY_FA = {"info": "اطلاع", "low": "کم", "medium": "متوسط", "high": "زیاد", "critical": "بحرانی"}
# What each pattern means, in plain Persian (the engine's own detail line follows it).
FINDING_MEANING = {
    "pass_through": "تقریباً هرچه دریافت کرده، کمی بعد فرستاده است؛ کیف پول را نگه نمی‌دارد و فقط از آن عبور می‌دهد.",
    "rapid_movement": "فاصلهٔ بین دریافت و ارسال پول بسیار کوتاه است.",
    "fan_in": "در مدت کوتاهی از تعداد زیادی آدرس پول گرفته است (الگوی جمع‌آوری).",
    "fan_out": "در مدت کوتاهی به تعداد زیادی آدرس پول فرستاده است (الگوی پخش).",
    "dormant_reactivated": "پس از یک دورهٔ طولانی بی‌تحرکی دوباره فعال شده است.",
    "exchange_exposure": "با کیف‌های صرافی تراکنش دارد؛ صرافی می‌تواند مالک حساب واریز را (با احراز هویت) مشخص کند.",
    "poisoning_target": "آدرس‌هایی شبیه طرف‌حساب‌های واقعی‌اش مبالغ ناچیز برایش فرستاده‌اند (حملهٔ Address Poisoning)؛ این انتقال‌ها در آمار حساب نشده‌اند.",
    "round_amounts": "بیشتر مبالغ رُند هستند.",
    "structuring": "چند انتقال درست زیر ۱۰٬۰۰۰ دارد (احتمال خردکردن مبلغ).",
    "new_high_volume": "کیف تازه‌ای است که حجم زیادی جابه‌جا کرده.",
    "likely_service": "با صدها آدرس تراکنش دارد؛ رفتار آن شبیه کیف صرافی یا سرویس پرداخت است.",
    "short_lived_intermediary": "عمر کوتاهی داشته و فقط واسطهٔ عبور پول بوده است.",
}  # fmt: skip


# --- formatting -----------------------------------------------------------------------


def num(value: Decimal | float | int | None, digits: int = 2) -> str:
    """"۱۰٬۳۰۴٫۳۵": Persian digits, thousands separators, up to `digits` decimals."""
    if value is None:
        return "—"
    v = Decimal(str(value))
    text = f"{v:,.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return fa_digits(text).replace(",", "٬").replace(".", "٫")


def count(value: int) -> str:
    return fa_digits(f"{value:,}").replace(",", "٬")


def amount(value: Decimal | None, token: str) -> str:
    return f'<span class="amt"><bdi>{num(value)}</bdi> <bdi class="tok">{escape(token)}</bdi></span>'


def usd(value: Decimal | None) -> str:
    return "—" if value is None else f"<bdi>{num(value, 0 if abs(value) >= 100 else 2)}</bdi> دلار"


def toman(value: Decimal | None) -> str:
    return "—" if value is None else f"<bdi>{num(value, 0)}</bdi> تومان"


def day(d: datetime | None) -> str:
    """Jalali date with the Gregorian one beside it."""
    if d is None:
        return "—"
    return f'<span class="date">{jalali(d)} <bdi class="greg">{d:%Y-%m-%d}</bdi></span>'


def moment(d: datetime) -> str:
    return f'<span class="date">{jalali(d)} <bdi class="greg">{d:%Y-%m-%d %H:%M}</bdi></span>'


def mono(text: str) -> str:
    return f'<bdi class="mono">{escape(text)}</bdi>'


def tx_link(tx_hash: str) -> str:
    return (
        f'<a class="mono tx" href="{TX_URL.format(escape(tx_hash))}" target="_blank" rel="noopener" '
        f'title="{escape(tx_hash)}"><bdi>{escape(tx_hash[:8])}…{escape(tx_hash[-6:])}</bdi></a>'
    )


class _Ctx:
    """Lookups shared by the page builders."""

    def __init__(self, inv: Investigation):
        self.inv = inv
        self.index = {m.address: m.index for m in inv.members}
        self.focus = {p.address: i for i, p in enumerate(inv.focus)}
        self.has_toman = inv.toman.rate is not None or any(
            t.toman_then is not None for ts in inv.focus_history.values() for t in ts
        )
        self.manual_toman = inv.toman.source == "manual"

    def owner(self, address: str) -> str | None:
        """Tag or label, else the owner worked out from evidence (marked as such)."""
        if address in self.inv.labels:
            return self.inv.labels[address]
        if address in self.inv.inferred:
            return f"{self.inv.inferred[address].owner} (استنباطی)"
        return None

    def chip(self, address: str) -> str:
        """"#4" in the wallet's colour for list members; a short address for others."""
        i = self.index.get(address)
        if i is None:
            return self.outside(address)
        cls = f"chip f{self.focus[address] % len(FOCUS_COLORS)}" if address in self.focus else "chip"
        if address not in self.focus and is_service(self.inv, address):
            cls += " svc"
        return f'<a class="{cls}" href="{ADDRESS_URL.format(escape(address))}" target="_blank" rel="noopener" title="{escape(address)}"><bdi>#{i}</bdi></a>'

    def outside(self, address: str) -> str:
        tag = self.owner(address)
        text = f'<a class="mono" href="{ADDRESS_URL.format(escape(address))}" target="_blank" rel="noopener" title="{escape(address)}"><bdi>{escape(short(address))}</bdi></a>'
        if tag:
            text += f' <bdi class="tag">{escape(tag)}</bdi>'
        return text

    def party(self, address: str) -> str:
        """Chip plus, for members, the tag (exchange name) when there is one."""
        text = self.chip(address)
        tag = self.owner(address) if address in self.index else None
        if tag:
            text += f' <bdi class="tag">{escape(tag)}</bdi>'
        return text

    def full(self, address: str) -> str:
        return f'<a class="mono" href="{ADDRESS_URL.format(escape(address))}" target="_blank" rel="noopener"><bdi>{escape(address)}</bdi></a>'

    def is_token(self, t: TransferRec) -> bool:
        inv = self.inv
        if inv.token_contract is not None:
            return t.token_contract == inv.token_contract
        return t.token_contract is None and t.token_symbol.upper() == inv.token.upper()

    def strength(self, address: str) -> tuple[str, str]:
        """(css class, Persian words) for how much a shared wallet says about a link."""
        if is_service(self.inv, address) or address in self.inv.inferred:
            return "weak", "ضعیف (صرافی/سرویس)"
        return "mid", "قابل‌توجه (کیف بدون برچسب)"


def table(head: list[str], rows: list[list[str]], cls: str = "") -> str:
    if not rows:
        return '<p class="muted">موردی یافت نشد.</p>'
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tbl {cls}"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def figure(g: GraphFile, caption: str, number: int) -> str:
    return (
        f'<figure id="fig-{escape(g.key)}"><a href="graphs/{g.png.name}" target="_blank">'
        f'<img src="graphs/{g.png.name}" alt="{escape(g.title)}" width="{g.width}" height="{g.height}" loading="lazy"></a>'
        f"<figcaption><b>نمودار {fa_digits(number)}.</b> {caption}"
        f'<span class="dl no-print"><a href="graphs/{g.png.name}" download>دانلود PNG</a>'
        f'<a href="graphs/{g.svg.name}" download>دانلود SVG</a></span></figcaption></figure>'
    )


# --- the facts the summary and the sections share --------------------------------------------


def pair_facts(ctx: _Ctx) -> list[dict]:
    """Direct transfers of the token between two focus wallets, per ordered pair."""
    pairs: dict[tuple[str, str], list[TransferRec]] = defaultdict(list)
    for t in ctx.inv.focus_transfers:
        if ctx.is_token(t):
            pairs[(t.from_address, t.to_address)].append(t)
    return [
        {
            "from": a,
            "to": b,
            "count": len(ts),
            "total": sum((t.amount for t in ts), Decimal(0)),
            "first": ts[0].timestamp,
            "last": ts[-1].timestamp,
        }
        for (a, b), ts in sorted(pairs.items(), key=lambda kv: -sum(t.amount for t in kv[1]))
    ]


def shared_with_focus(ctx: _Ctx) -> list[dict]:
    """Wallets (inside or outside the list) that dealt with two or more focus wallets."""
    inv = ctx.inv
    flows = focus_edges(inv)
    per: dict[str, dict[str, dict]] = defaultdict(dict)  # other -> focus -> amounts
    for (src, dst), (total, n) in flows.items():
        for f, other, key in ((src, dst, "sent"), (dst, src, "received")):
            if f in ctx.focus and other not in ctx.focus:
                cell = per[other].setdefault(f, {"sent": Decimal(0), "sent_n": 0, "received": Decimal(0), "received_n": 0})
                cell[key] += total
                cell[f"{key}_n"] += n
    out = []
    for other, by_focus in per.items():
        if len(by_focus) < 2:
            continue
        volume = sum((c["sent"] + c["received"] for c in by_focus.values()), Decimal(0))
        out.append({"address": other, "by_focus": by_focus, "volume": volume, "service": is_service(inv, other)})
    out.sort(key=lambda r: (r["service"], -len(r["by_focus"]), -r["volume"]))
    return out


def summary_sentences(ctx: _Ctx) -> list[str]:
    """The strongest facts, as Persian sentences (HTML)."""
    inv = ctx.inv
    token = escape(inv.token)
    out: list[str] = []
    pairs = pair_facts(ctx)
    for p in pairs:
        out.append(
            f"کیف {ctx.chip(p['from'])} در <b>{count(p['count'])} انتقال</b> مجموعاً <b>{amount(p['total'], inv.token)}</b> "
            f"مستقیماً به کیف {ctx.chip(p['to'])} فرستاده است؛ از {day(p['first'])} تا {day(p['last'])}. "
            "این قوی‌ترین ارتباط بین کیف‌های اصلی است."
        )
    linked = {frozenset((p["from"], p["to"])) for p in pairs}
    focus = [p.address for p in inv.focus]
    unlinked = [
        (a, b) for i, a in enumerate(focus) for b in focus[i + 1 :] if frozenset((a, b)) not in linked
    ]
    shared = shared_with_focus(ctx)
    def side(row: dict, wallet: str) -> str:
        c = row["by_focus"][wallet]
        bits = []
        if c["sent"]:
            bits.append(f"{amount(c['sent'], inv.token)} به آن فرستاده")
        if c["received"]:
            bits.append(f"{amount(c['received'], inv.token)} از آن گرفته")
        return f"{ctx.chip(wallet)} " + " و ".join(bits)

    for a, b in unlinked:
        common = [r for r in shared if a in r["by_focus"] and b in r["by_focus"]]
        personal = sorted(
            (r for r in common if not r["service"]),
            key=lambda r: min(sum(r["by_focus"][w][k] for k in ("sent", "received")) for w in (a, b)),
            reverse=True,
        )
        services = [r for r in common if r["service"]]
        text = f"بین کیف {ctx.chip(a)} و کیف {ctx.chip(b)} <b>هیچ انتقال مستقیم {token}</b> دیده نشد"
        if personal:
            top = personal[0]
            text += (
                f"؛ اما هر دو با کیف بدون‌برچسب {ctx.outside(top['address'])} تراکنش داشته‌اند "
                f"({side(top, a)}؛ {side(top, b)})"
                + (f" و {count(len(personal) - 1)} کیف بدون‌برچسب مشترک دیگر هم دارند" if len(personal) > 1 else "")
            )
        if services:
            names = "، ".join(
                f"<bdi>{escape(short_tag(inv.labels.get(r['address'], short(r['address']))))}</bdi>" for r in services[:5]
            )
            text += f"{'. همچنین' if personal else '؛ فقط'} {count(len(services))} صرافی مشترک دارند ({names}) که شاهد ضعیفی است"
        out.append(text + ".")
    for p in inv.focus:
        t = _token_totals(ctx, p)
        if t is None:
            continue
        sentence = (
            f"کیف {ctx.chip(p.address)} در مجموع <b>{amount(t.total_in, inv.token)}</b> دریافت و "
            f"<b>{amount(t.total_out, inv.token)}</b> ارسال کرده است ({count(t.count_in + t.count_out)} انتقال با "
            f"{count(p.counterparty_count)} طرف‌حساب، از {day(p.first_seen)} تا {day(p.last_seen)})"
        )
        if p.top_receivers and t.total_out > 0:
            top = p.top_receivers[:2]
            share = sum((r.amount for r in top), Decimal(0)) / t.total_out * 100
            if share >= 60:
                names = " و ".join(ctx.party(r.address) for r in top)
                sentence += f"؛ <b>{num(share, 0)}٪</b> خروجی آن به {names} رفته است"
        out.append(sentence + ".")
    contracts = [p for p in inv.focus if p.is_contract]
    for p in contracts:
        inf = inv.inferred.get(p.address)
        if inf:
            out.append(
                f"کیف {ctx.chip(p.address)} یک <b>حساب قرارداد هوشمند</b> است که آدرس {ctx.full(inv.contract_creators.get(p.address, ''))} "
                f"آن را ساخته؛ همان آدرس در {day(inf.evidence_date) if inf.evidence_date else '—'} به آدرسِ سوزاندن حملهٔ <b>{escape(inf.owner)}</b> پول فرستاده است، "
                f"یعنی {ctx.chip(p.address)} یک <b>آدرس واریز {escape(inf.owner)}</b> است (حساب کاربری در آن صرافی)."
            )
        else:
            out.append(
                f"کیف {ctx.chip(p.address)} یک <b>حساب قرارداد هوشمند</b> است (نه کیف شخصی معمولی)؛ این نوع حساب را معمولاً "
                "یک سرویس (صرافی یا درگاه پرداخت) برای هر مشتری می‌سازد و برداشت از آن با دستور همان سرویس انجام می‌شود."
            )
    for p in inv.focus:
        exits = [e for e in inv.exits if any(f.index == p.index for f in e.flows)]
        named = [e for e in exits if ctx.owner(e.address) and not (e.index and e.address in ctx.focus)]
        if not named:
            continue
        bits = []
        for e in named[:3]:
            f = next(x for x in e.flows if x.index == p.index)
            bits.append(
                f"<b>{escape(ctx.owner(e.address))}</b> ({amount(f.total, inv.token)} در {count(f.count)} انتقال، "
                f"{num(f.share, 0)}٪ خروجی، {day(f.first_seen)} تا {day(f.last_seen)})"
            )
        out.append(f"خروجی کیف {ctx.chip(p.address)} به " + "؛ ".join(bits) + " رفته است.")
    for p in inv.focus:
        entries = [e for e in inv.entries if any(f.index == p.index for f in e.flows) and e.address not in ctx.focus]
        named = [e for e in entries if ctx.owner(e.address)]
        if not named:
            continue
        bits = []
        for e in named[:3]:
            f = next(x for x in e.flows if x.index == p.index)
            bits.append(f"<b>{escape(ctx.owner(e.address))}</b> ({amount(f.total, inv.token)}، {num(f.share, 0)}٪ ورودی)")
        out.append(f"ورودی کیف {ctx.chip(p.address)} از " + "، ".join(bits) + " آمده است.")
    services = [m for m in inv.members if m.likely_service or is_service(inv, m.address)]
    if services:
        names = "، ".join(ctx.party(m.address) for m in services)
        out.append(
            f"از {count(len(inv.members))} کیف فهرست، {count(len(services))} کیف رفتار صرافی/سرویس دارند ({names})؛ "
            "انتقال با این کیف‌ها نشانهٔ مالکیت مشترک نیست و فقط <b>شاهد ضعیف</b> به شمار می‌آید."
        )
    if inv.links_all.groups:
        group = inv.links_all.groups[0]
        out.append(
            f"<b>{count(len(group))} کیف</b> از فهرست با جریان پول (مستقیم یا از طریق کیف واسط غیرصرافی) به هم وصل‌اند؛ "
            + (
                "کیف‌های "
                + "، ".join(ctx.chip(m.address) for m in inv.members if m.partners == 0 and m.group is None and not m.likely_service)
                + " هیچ ارتباطی با بقیه ندارند."
                if any(m.partners == 0 and m.group is None and not m.likely_service for m in inv.members)
                else "هیچ کیفی جدا نمانده است."
            )
        )
    for mp in inv.most_connected:
        if mp.likely_service:
            continue
        out.append(
            f"در میان کیف‌های غیراصلی، کیف {ctx.chip(mp.address)} بیشترین جابه‌جایی را با اعضای فهرست دارد: "
            f"{amount(mp.received_from_members, inv.token)} دریافت و {amount(mp.sent_to_members, inv.token)} ارسال."
        )
        break
    frozen = [m for m in inv.members if m.usdt_frozen]
    checked = [m for m in inv.members if m.usdt_frozen is not None]
    if frozen:
        out.append("کیف‌های " + "، ".join(ctx.chip(m.address) for m in frozen) + " توسط Tether <b>مسدود</b> شده‌اند.")
    elif checked:
        out.append(f"هیچ‌یک از {count(len(checked))} کیف بررسی‌شده در فهرست مسدودی Tether نیست.")
    verified = [p for p in inv.focus if p.verification and p.verification.status == "verified"]
    if verified and len(verified) == len(inv.focus):
        out.append(
            f"تعداد و مجموع انتقال‌های {token} هر {count(len(verified))} کیف اصلی با داده‌های TronScan <b>دقیقاً مطابقت</b> دارد."
        )
    return out


def _token_totals(ctx: _Ctx, p: FocusProfile):
    inv = ctx.inv
    for t in p.totals:
        if (inv.token_contract is not None and t.token_contract == inv.token_contract) or (
            inv.token_contract is None and t.token_contract is None and t.token_symbol.upper() == inv.token.upper()
        ):
            return t
    return None


# --- sections ------------------------------------------------------------------------------------


def _transfer_rows(ctx: _Ctx, transfers: list[TransferRec], numbered: bool = True) -> tuple[list[str], list[list[str]]]:
    head = (["#"] if numbered else []) + ["تاریخ (UTC)", "از", "به", "مبلغ", "ارزش روز (دلار)"]
    if ctx.has_toman:
        head.append("ارزش به تومان (نرخ امروز)" if ctx.manual_toman else "ارزش روز (تومان)")
    head.append("تراکنش")
    rows = []
    for i, t in enumerate(transfers, start=1):
        row = ([count(i)] if numbered else []) + [
            moment(t.timestamp),
            ctx.party(t.from_address),
            ctx.party(t.to_address),
            amount(t.amount, t.token_symbol),
            usd(t.usd_then),
        ]
        if ctx.has_toman:
            row.append(toman(t.toman_then))
        row.append(tx_link(t.tx_hash))
        rows.append(row)
    return head, rows


def _exit_table(ctx: _Ctx, items, role: str) -> str:
    """One card per entry/exit wallet (a table would be too wide for the evidence)."""
    inv = ctx.inv
    cards = []
    for e in items:
        owner = ctx.owner(e.address)
        head = (ctx.party(e.address) if e.index else ctx.full(e.address))
        badges = []
        if owner and not e.index:
            badges.append(f'<bdi class="tag">{escape(owner)}</bdi>')
        if e.is_contract:
            badges.append('<span class="pill mid">حساب قرارداد هوشمند</span>')
        if e.transactions:
            badges.append(f'<span class="pill">{count(e.transactions)} تراکنش در کل</span>')
        lines = []
        for f in e.flows:
            txs = "، ".join(tx_link(t.tx_hash) for t in f.largest)
            lines.append(
                f"<li>{ctx.chip(next(p.address for p in inv.focus if p.index == f.index))} "
                f"{'به آن' if role == 'exit' else 'از آن'} <b>{amount(f.total, inv.token)}</b> در {count(f.count)} انتقال "
                f"({num(f.share, 0)}٪ {'خروجی' if role == 'exit' else 'ورودی'} این کیف اصلی)، از {day(f.first_seen)} تا {day(f.last_seen)}"
                f'<div class="muted">بزرگ‌ترین تراکنش‌ها: {txs}</div></li>'
            )
        extra = []
        if e.inference:
            extra.append(f'<p class="note"><b>چرا {escape(e.inference.owner)}؟</b> {_basis_fa(ctx, e.inference)}</p>')
        if e.downstream:
            items_fa = "، ".join(
                f"{ctx.outside(d.address)} ({amount(d.amount, inv.token)}، {count(d.count)} انتقال)" for d in e.downstream[:3]
            )
            extra.append(
                f'<p class="muted">مقصد بعدی: در نمونهٔ {count(e.downstream_sampled)} انتقال خروجی اخیرِ این کیف، پول بیشتر به {items_fa} رفته است.</p>'
            )
        cards.append(
            f'<div class="box exit"><div class="exit-head"><span>{head}</span><span class="badges">{" ".join(badges)}</span>'
            f'<span class="sum">{amount(e.total, inv.token)}</span></div><ul>{"".join(lines)}</ul>{"".join(extra)}</div>'
        )
    return "".join(cards)


def _basis_fa(ctx: _Ctx, inf) -> str:
    """The inference's English basis, said in Persian with the evidence linked."""
    inv = ctx.inv
    b = inf.basis
    tx = tx_link(inf.evidence_tx) if inf.evidence_tx else ""
    when = day(inf.evidence_date) if inf.evidence_date else ""
    if b.startswith("sent "):
        amount_text = b.split(" ")[1]
        return (
            f"این کیف در {when} مبلغ <bdi>{escape(amount_text)}</bdi> {escape(inv.token)} به آدرس سوزاندن حملهٔ {escape(inf.owner)} فرستاده است "
            f"(تراکنش {tx}). در آن حمله فقط کیف‌های تحت کنترل {escape(inf.owner)} خالی شدند؛ پس این کیف متعلق به {escape(inf.owner)} بوده است."
        )
    if b.startswith("deposit contract created by "):
        creator = b.split(" ")[4].rstrip(",")
        return (
            f"این حساب قرارداد را آدرس {ctx.full(creator)} ساخته است و همان آدرس در {when} به آدرس سوزاندن حملهٔ {escape(inf.owner)} پول فرستاده "
            f"(تراکنش {tx})؛ یعنی سازنده‌اش {escape(inf.owner)} است و این حساب یک آدرس واریز {escape(inf.owner)} است."
        )
    if b.startswith("receives "):
        return (
            f"{escape(b.split(' ')[1])} از خروجی حساب قرارداد واریزِ {escape(inf.owner)} به این کیف می‌رود؛ چنین کیفی کیف تجمیع همان صرافی است "
            + (f"(شاهد: تراکنش {tx})." if tx else ".")
        )
    return f'<span class="en" dir="ltr">{escape(b)}</span>'


def section_exits(ctx: _Ctx, graphs: dict[str, GraphFile], fig_no, number: str) -> str:
    inv = ctx.inv
    token = escape(inv.token)
    parts = [f'<section id="exits"><h2>{number} مسیر خروج و ورود پول کیف‌های اصلی</h2>']
    parts.append(
        '<p>برای هر کیف اصلی، طرف‌حساب‌هایی که بیشترِ پول از آن‌ها آمده یا به آن‌ها رفته، نام‌گذاری شده‌اند: با برچسب عمومی TronScan، '
        "با فهرست داخلی، یا با شواهد زنجیره‌ای (مثلاً کیفی که در حملهٔ ۲۸ خرداد ۱۴۰۴ به نوبیتکس از آن پول به آدرس سوزاندن رفته، "
        "کیف نوبیتکس بوده است). مواردی که «استنباطی» نوشته شده از این شواهد به دست آمده‌اند و شاهدشان ذکر شده است.</p>"
    )
    if "exit_points" in graphs:
        parts.append(
            figure(
                graphs["exit_points"],
                f"ورود و خروج {token} کیف‌های اصلی: ستون چپ منابع، ستون وسط کیف‌های اصلی، ستون راست مقصدها. ارتفاع هر نوار متناسب با مبلغ است؛ "
                "مربع زرد یعنی صرافی/سرویس (با برچسب یا مالک استنباط‌شده)، خاکستری یعنی کیف بدون نام.",
                fig_no(),
            )
        )
    parts.append(f"<h3>مقصد پول (خروجی {token})</h3>")
    parts.append(_exit_table(ctx, inv.exits, "exit") if inv.exits else '<p class="muted">خروجی‌ای ثبت نشده.</p>')
    parts.append(f"<h3>منبع پول (ورودی {token})</h3>")
    parts.append(_exit_table(ctx, inv.entries, "entry") if inv.entries else '<p class="muted">ورودی‌ای ثبت نشده.</p>')
    parts.append(
        '<p class="note">«بزرگ‌ترین‌ها» شناسهٔ سه تراکنش بزرگ هر رابطه است و با کلیک در TronScan باز می‌شود. '
        "«مقصد بعدی» از نمونهٔ تازه‌ترین انتقال‌های خروجیِ آن کیف گرفته شده و برای کیف‌های بسیار پرتراکنش فقط تصویری تقریبی می‌دهد.</p>"
    )
    parts.append("</section>")
    return "".join(parts)


def section_connections(ctx: _Ctx, graphs: dict[str, GraphFile], fig_no, number: str = "۲.") -> str:
    inv = ctx.inv
    parts = [f'<section id="connections"><h2>{number} ارتباط کیف‌های اصلی با یکدیگر</h2>']
    parts.append(
        figure(
            graphs["focus_network"],
            "شبکهٔ ارتباط کیف‌های اصلی: دایره‌های رنگی کیف‌های اصلی‌اند، مربع بنفش کیف فهرست با رفتار صرافی، "
            "مربع زرد صرافیِ بیرون از فهرست و دایرهٔ خاکستری کیف بیرونیِ بدون برچسب. جهت پیکان جهت حرکت پول و "
            f"ضخامت آن متناسب با مبلغ {escape(inv.token)} است. کیفی که بین دو کیف اصلی رسم شده با هر دو تراکنش داشته است.",
            fig_no(),
        )
    )
    parts.append("<h3>انتقال‌های مستقیم بین کیف‌های اصلی (به ترتیب زمان)</h3>")
    if inv.focus_transfers:
        head, rows = _transfer_rows(ctx, inv.focus_transfers)
        parts.append(table(head, rows))
        pairs = pair_facts(ctx)
        if pairs:
            parts.append(
                '<p class="note">جمع: '
                + "؛ ".join(
                    f"از {ctx.chip(p['from'])} به {ctx.chip(p['to'])}: {count(p['count'])} انتقال، {amount(p['total'], inv.token)}"
                    for p in pairs
                )
                + ". روی شناسهٔ هر تراکنش بزنید تا در TronScan باز شود.</p>"
            )
    else:
        parts.append('<p class="muted">هیچ انتقال مستقیمی بین کیف‌های اصلی دیده نشد.</p>')

    shared = shared_with_focus(ctx)
    parts.append("<h3>ارتباط‌های غیرمستقیم: کیف‌هایی که با دو یا چند کیف اصلی تراکنش داشته‌اند</h3>")
    if shared:
        head = ["کیف مشترک"] + [f"با {ctx.chip(p.address)}" for p in inv.focus] + ["وزن شاهد"]
        rows = []
        for r in shared[:25]:
            cells = [ctx.party(r["address"])]
            for p in inv.focus:
                c = r["by_focus"].get(p.address)
                if c is None:
                    cells.append('<span class="muted">—</span>')
                    continue
                bits = []
                if c["sent"]:
                    bits.append(f"ارسال به آن: {amount(c['sent'], inv.token)} ({count(c['sent_n'])})")
                if c["received"]:
                    bits.append(f"دریافت از آن: {amount(c['received'], inv.token)} ({count(c['received_n'])})")
                cells.append("<br>".join(bits))
            cls, words = ctx.strength(r["address"])
            cells.append(f'<span class="pill {cls}">{words}</span>')
            rows.append(cells)
        parts.append(table(head, rows))
        parts.append(
            '<p class="note">«ارسال به آن» یعنی کیف اصلی به کیف مشترک پول فرستاده؛ عدد داخل پرانتز تعداد انتقال است. '
            "صرافی‌ها پول هزاران مشتری را در یک کیف جمع می‌کنند، پس داشتن صرافی مشترک به‌تنهایی ارتباط دو نفر را نشان نمی‌دهد. "
            "کیف بدون برچسب می‌تواند شخص سوم، صراف غیررسمی یا سرویسِ برچسب‌نخورده باشد و ارزش پیگیری دارد.</p>"
        )
    else:
        parts.append('<p class="muted">کیف مشترکی یافت نشد.</p>')

    hops = [p for p in inv.links_focus.paths if len(p.via) >= 2]
    if hops:
        parts.append("<h3>مسیرهای سه‌مرحله‌ای بین کیف‌های اصلی</h3>")
        rows = [
            [
                ctx.chip(p.from_address),
                " ← ".join(ctx.party(v) for v in p.via),
                ctx.chip(p.to_address),
                amount(p.amount_in, p.token_symbol),
                amount(p.amount_out, p.token_symbol),
            ]
            for p in hops
        ]
        parts.append(table(["مبدأ", "کیف‌های واسط (به ترتیب)", "مقصد", "ورودی به مسیر", "خروجی از مسیر به مقصد"], rows))
        parts.append(
            '<p class="note">این مسیرها فقط نشان می‌دهند که چنین زنجیره‌ای از انتقال‌ها وجود دارد؛ مبلغ ورودی و خروجی '
            "لزوماً همان پول نیست، مخصوصاً وقتی یکی از واسط‌ها صرافی باشد.</p>"
        )
    parts.append("</section>")
    return "".join(parts)


def section_timeline(ctx: _Ctx, graphs: dict[str, GraphFile], fig_no, number: str = "۳.") -> str:
    inv = ctx.inv
    rows = []
    for p in inv.focus:
        ts = [t for t in inv.focus_history.get(p.address, []) if ctx.is_token(t)]
        months = [m for m in p.monthly.get(inv.token, []) if m.count_in + m.count_out]
        busiest = max(months, key=lambda m: m.amount_in + m.amount_out, default=None)
        rows.append(
            [
                ctx.chip(p.address),
                day(ts[0].timestamp) if ts else "—",
                day(ts[-1].timestamp) if ts else "—",
                count(len(months)),
                f'<bdi class="greg">{busiest.period}</bdi> — {amount(busiest.amount_in + busiest.amount_out, inv.token)}'
                if busiest
                else "—",
            ]
        )
    return (
        f'<section id="timeline"><h2>{number} روند فعالیت کیف‌های اصلی در طول زمان</h2>'
        + figure(
            graphs["joint_timeline"],
            f"هر ردیف یک کیف اصلی است و هر دایره یک انتقال {escape(inv.token)}: دایرهٔ توپر بالای خط = دریافت، دایرهٔ توخالی "
            "زیر خط = ارسال؛ اندازهٔ دایره متناسب با مبلغ است. خط‌های عمودی سیاه پرداخت مستقیم یک کیف اصلی به کیف اصلی دیگر را نشان می‌دهند.",
            fig_no(),
        )
        + table(
            ["کیف", f"اولین انتقال {escape(inv.token)}", f"آخرین انتقال {escape(inv.token)}", "ماه‌های فعال", "پرکارترین ماه (ورودی + خروجی)"],
            rows,
        )
        + "</section>"
    )


def _party_table(ctx: _Ctx, parties, total: Decimal, token: str, verb: str) -> str:
    rows = []
    for i, p in enumerate(parties, start=1):
        share = p.amount / total * 100 if total else Decimal(0)
        rows.append(
            [
                count(i),
                ctx.party(p.address),
                amount(p.amount, token),
                f"<bdi>{num(share, 1)}٪</bdi>",
                count(p.count),
                day(p.first_seen),
                day(p.last_seen),
            ]
        )
    return table(["#", verb, "مبلغ", "سهم", "تعداد", "اولین", "آخرین"], rows)


def _findings(findings) -> str:
    if not findings:
        return '<p class="muted">الگوی پرریسکی دیده نشد.</p>'
    items = []
    for f in findings:
        items.append(
            f'<li><span class="pill sev-{escape(f.severity.value)}">{SEVERITY_FA.get(f.severity.value, f.severity.value)}</span> '
            f"<b>{escape(FINDING_FA.get(f.code, f.title))}</b>"
            + (f" — {FINDING_MEANING[f.code]}" if f.code in FINDING_MEANING else "")
            + f'<div class="en" dir="ltr">{escape(f.detail)}</div></li>'
        )
    return f'<ul class="findings">{"".join(items)}</ul>'


def _max_held(ctx: _Ctx, p: FocusProfile) -> tuple[Decimal, datetime | None]:
    held, best, when = Decimal(0), Decimal(0), None
    for t in ctx.inv.focus_history.get(p.address, []):
        if not ctx.is_token(t):
            continue
        held += t.amount if t.to_address == p.address else -t.amount
        if held > best:
            best, when = held, t.timestamp
    return best, when


def section_profile(ctx: _Ctx, p: FocusProfile, graphs: dict[str, GraphFile], fig_no, number: str) -> str:
    inv = ctx.inv
    token = inv.token
    t = _token_totals(ctx, p)
    total_in = t.total_in if t else Decimal(0)
    total_out = t.total_out if t else Decimal(0)
    balance = next((b for b in p.balances if inv.token_contract and b.token_contract == inv.token_contract), None)
    native = next((b for b in p.balances if b.token_contract is None), None)
    held, held_when = _max_held(ctx, p)
    life = (p.last_seen - p.first_seen).days if p.first_seen and p.last_seen else None
    frozen = {True: "مسدود شده", False: "مسدود نیست", None: "نامشخص"}[p.usdt_frozen]
    cards = [
        ("دریافتی", amount(total_in, token), f"{count(t.count_in) if t else '۰'} انتقال" + (f" · {usd(t.usd_in_then)}" if t and t.usd_in_then is not None else "")),
        ("ارسالی", amount(total_out, token), f"{count(t.count_out) if t else '۰'} انتقال" + (f" · {usd(t.usd_out_then)}" if t and t.usd_out_then is not None else "")),
        ("طرف‌حساب‌ها", count(p.counterparty_count), f"{count(p.stats.distinct_senders)} فرستنده · {count(p.stats.distinct_receivers)} گیرنده"),
        ("دورهٔ فعالیت", f"{count(life)} روز" if life is not None else "—", f"{day(p.first_seen)} تا {day(p.last_seen)}"),
        (
            "موجودی فعلی",
            amount(balance.amount if balance else Decimal(0), token),
            (f"{amount(native.amount, native.token_symbol)}" if native else "") + (f" · بیشترین موجودی هم‌زمان: {amount(held, token)}" if held else ""),
        ),
        ("امتیاز ریسک", f"<bdi>{count(p.risk_score)}</bdi> از ۱۰۰", f"سطح: {LEVEL_FA.get(p.risk_level, p.risk_level)} · Tether: {frozen}"),
    ]
    color = f"f{ctx.focus[p.address] % len(FOCUS_COLORS)}"
    parts = [
        f'<section id="wallet-{p.index}" class="profile"><h2>{number} پروفایل کیف {ctx.chip(p.address)}</h2>',
        f'<p class="addr">{ctx.full(p.address)}'
        + (f' <bdi class="tag">{escape(p.label)}</bdi>' if p.label else "")
        + (' <span class="pill mid">حساب قرارداد هوشمند</span>' if p.is_contract else "")
        + "</p>",
        f'<div class="cards {color}">'
        + "".join(f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>' for k, v, s in cards)
        + "</div>",
    ]
    if p.is_contract:
        parts.append(
            '<p class="note warn">این آدرس یک حساب قرارداد هوشمند است، نه کیف شخصیِ دارای کلید خصوصی. چنین حساب‌هایی را معمولاً یک '
            "سرویس (صرافی یا درگاه پرداخت) به‌عنوان «آدرس واریز» برای مشتری می‌سازد و خروج پول از آن با فراخوانی قرارداد توسط همان "
            "سرویس انجام می‌شود. بنابراین «مالک» این آدرس می‌تواند مشتریِ آن سرویس باشد و مقصد خروجی‌های آن، کیف تجمیع سرویس است.</p>"
        )
    if p.truncated:
        parts.append('<p class="note warn">تاریخچهٔ این کیف از سقف دریافت بیشتر بود و کامل نیست.</p>')
    parts.append(
        figure(
            graphs[f"flow_{p.index}"],
            f"منابع و مقصدهای {escape(token)} کیف {ctx.chip(p.address)}: سمت چپ فرستنده‌ها، سمت راست گیرنده‌ها؛ ضخامت هر نوار متناسب با مبلغ است.",
            fig_no(),
        )
    )
    parts.append(
        figure(
            graphs[f"timeline_{p.index}"],
            f"فعالیت ماهانهٔ کیف {ctx.chip(p.address)}: ستون سبز دریافتی و ستون قرمز ارسالیِ هر ماه؛ نمودار پایین مقدار "
            f"{escape(token)} نگه‌داشته‌شده در کیف پس از هر انتقال را نشان می‌دهد.",
            fig_no(),
        )
    )
    head = ["توکن", "دریافتی", "تعداد", "ارسالی", "تعداد", "ارزش دریافتی در زمان انتقال", "ارزش ارسالی در زمان انتقال"]
    rows = []
    for r in p.totals:
        if r.usd_in_then is None and r.usd_out_then is None and not (r.token_contract is None):
            continue  # unpriced minor tokens
        cells = [
            f"<bdi>{escape(r.token_symbol)}</bdi>",
            f"<bdi>{num(r.total_in)}</bdi>",
            count(r.count_in),
            f"<bdi>{num(r.total_out)}</bdi>",
            count(r.count_out),
            usd(r.usd_in_then) + (f"<br>{toman(r.toman_in_then)}" if ctx.has_toman and r.toman_in_then is not None else ""),
            usd(r.usd_out_then) + (f"<br>{toman(r.toman_out_then)}" if ctx.has_toman and r.toman_out_then is not None else ""),
        ]
        rows.append(cells)
    parts.append("<h3>جمع ورودی و خروجی</h3>" + table(head, rows))
    other = [r for r in p.totals if r.usd_in_then is None and r.usd_out_then is None and r.token_contract is not None]
    if other or p.spam_count:
        bits = []
        if other:
            bits.append(f"{count(len(other))} توکن دیگرِ بدون قیمت ({'، '.join(f'<bdi>{escape(r.token_symbol)}</bdi>' for r in other[:6])}) در جدول نیامده")
        if p.spam_count:
            bits.append(f"{count(p.spam_count)} انتقال هرزنامه (توکن جعلی/تبلیغاتی یا مبلغ صفر) کنار گذاشته شده")
        parts.append(f'<p class="note">{"؛ ".join(bits)}.</p>')
    parts.append(f"<h3>فرستنده‌های اصلی ({escape(token)})</h3>" + _party_table(ctx, p.top_senders, total_in, token, "فرستنده"))
    parts.append(f"<h3>گیرنده‌های اصلی ({escape(token)})</h3>" + _party_table(ctx, p.top_receivers, total_out, token, "گیرنده"))
    head, rows = _transfer_rows(ctx, p.largest)
    parts.append(f"<h3>بزرگ‌ترین انتقال‌ها ({escape(token)})</h3>" + table(head, rows))
    parts.append("<h3>الگوهای رفتاری و ریسک</h3>" + _findings(p.findings))
    if held_when:
        hours = p.stats.median_holding_hours
        parts.append(
            f'<p class="note">بیشترین {escape(token)} که هم‌زمان در این کیف بوده {amount(held, token)} است ({day(held_when)})'
            + (f"؛ میانهٔ زمان نگهداری پول پیش از ارسال: <bdi>{num(hours, 1)}</bdi> ساعت" if hours is not None else "")
            + ".</p>"
        )
    parts.append("</section>")
    return "".join(parts)


def _member_profile(ctx: _Ctx, mp: MemberProfile) -> str:
    inv = ctx.inv
    rows = [
        [ctx.party(pf.address), amount(pf.sent, inv.token), amount(pf.received, inv.token), count(pf.count), day(pf.first_seen), day(pf.last_seen)]
        for pf in mp.partners
    ]
    note = ""
    if mp.likely_service:
        note = (
            '<p class="note warn">این کیف رفتار صرافی/سرویس دارد'
            + (f" (برچسب TronScan: <bdi>{escape(inv.labels[mp.address])}</bdi>)" if mp.address in inv.labels else "")
            + f" و با {count(mp.counterparty_count)} آدرس تراکنش داشته است؛ انتقال‌های آن فقط شاهد ضعیف ارتباط هستند"
            + (" (تاریخچهٔ آن کامل دریافت نشده)" if mp.truncated else "")
            + ".</p>"
        )
    facts = (
        f"فعالیت از {day(mp.first_seen)} تا {day(mp.last_seen)} · کل دریافتی {amount(mp.token_in, inv.token)} · "
        f"کل ارسالی {amount(mp.token_out, inv.token)} · موجودی فعلی {amount(mp.balance, inv.token) if mp.balance is not None else '—'} · "
        f"امتیاز ریسک <bdi>{count(mp.risk_score)}</bdi> ({LEVEL_FA.get(mp.risk_level, mp.risk_level)})"
    )
    return (
        f'<div class="box"><h4>کیف {ctx.party(mp.address)}</h4><p class="addr">{ctx.full(mp.address)}</p>'
        f"<p>با اعضای فهرست: دریافت {amount(mp.received_from_members, inv.token)} و ارسال {amount(mp.sent_to_members, inv.token)}.</p>"
        f'<p class="muted">{facts}</p>{note}'
        + table(["طرف مقابل", "ارسال به او", "دریافت از او", "تعداد", "اولین", "آخرین"], rows)
        + "</div>"
    )


def section_list(ctx: _Ctx, graphs: dict[str, GraphFile], fig_no, number: str = "۵.") -> str:
    inv = ctx.inv
    token = escape(inv.token)
    parts = [f'<section id="list"><h2>{number} تحلیل کل فهرست ({count(len(inv.members))} کیف)</h2>']
    parts.append(
        figure(
            graphs["list_network"],
            "همهٔ کیف‌های فهرست به ترتیب شماره روی دایره چیده شده‌اند. پیکان پیوسته انتقال مستقیم بین دو کیف فهرست است؛ "
            "خط‌چین، کیف بیرونیِ بدون برچسبی را نشان می‌دهد که چند کیف غیرصرافی فهرست با آن تراکنش داشته‌اند. "
            "دایرهٔ کم‌رنگ یعنی هیچ ارتباطی با بقیه یافت نشد.",
            fig_no(),
        )
    )
    rows = []
    for m in inv.members:
        kind = "اصلی" if m.is_focus else ("صرافی/سرویس" if (m.likely_service or is_service(inv, m.address)) else "عادی")
        rows.append(
            [
                ctx.chip(m.address),
                ctx.full(m.address) + (f' <bdi class="tag">{escape(inv.labels[m.address])}</bdi>' if m.address in inv.labels else ""),
                kind,
                count(m.transfer_count) + ("+" if m.truncated else ""),
                count(m.counterparty_count),
                amount(m.sent_to_members, inv.token) if m.sent_to_members else "—",
                amount(m.received_from_members, inv.token) if m.received_from_members else "—",
                count(m.partners),
                {True: "مسدود", False: "خیر", None: "نامشخص"}[m.usdt_frozen],
            ]
        )
    parts.append("<h3>اعضای فهرست</h3>")
    parts.append(
        table(
            ["#", "آدرس", "نوع", "انتقال‌ها", "طرف‌حساب‌ها", "ارسال به اعضا", "دریافت از اعضا", "اعضای مرتبط", "مسدودی Tether"],
            rows,
        )
    )
    if any(m.truncated for m in inv.members):
        parts.append('<p class="note">علامت + یعنی کیف بیش از سقف دریافت تراکنش دارد و فقط تازه‌ترین انتقال‌هایش بررسی شده است.</p>')

    parts.append(
        figure(
            graphs["matrix_heatmap"],
            f"ماتریس انتقال {token} بین اعضا: هر سطر فرستنده و هر ستون گیرنده است؛ عدد هر خانه مجموع مبلغ و زیر آن تعداد انتقال. "
            "رنگ تیره‌تر یعنی مبلغ بیشتر (مقیاس رنگ لگاریتمی). جمع ارسالی هر کیف در ستون راست و جمع دریافتی در سطر پایین آمده است.",
            fig_no(),
        )
    )
    rows = [
        [
            ctx.party(link.from_address),
            ctx.party(link.to_address),
            amount(link.total, link.token_symbol),
            count(link.count),
            day(link.first_seen),
            day(link.last_seen),
        ]
        for link in inv.links_all.direct
    ]
    parts.append("<h3>همهٔ انتقال‌های مستقیم بین اعضا (جمع هر جفت)</h3>")
    parts.append(table(["از", "به", "مجموع", "تعداد", "اولین", "آخرین"], rows))

    parts.append(
        figure(
            graphs["ranking_bar"],
            f"رتبه‌بندی کیف‌ها بر اساس مجموع {token} جابه‌جاشده با دیگر اعضای فهرست: بخش سبز دریافتی از اعضا و بخش قرمز ارسالی به اعضا.",
            fig_no(),
        )
    )
    if inv.most_connected:
        parts.append("<h3>کیف‌های غیراصلی با بیشترین جابه‌جایی درون فهرست</h3>")
        parts.extend(_member_profile(ctx, mp) for mp in inv.most_connected)

    hubs = [s for s in inv.links_all.shared if not is_service(inv, s.address) and s.token_symbol.upper() == inv.token.upper()]
    if hubs:
        rows = []
        for s in hubs[:12]:
            role = "فرستندهٔ مشترک" if s.role.value == "common_source" else "گیرندهٔ مشترک"
            who = "، ".join(f"{ctx.chip(m.address)} ({amount(m.amount, inv.token)})" for m in s.members)
            rows.append([ctx.outside(s.address), role, count(len(s.members)), amount(s.total, inv.token), who])
        parts.append("<h3>کیف‌های بیرونیِ بدون برچسب که با چند عضو فهرست تراکنش داشته‌اند</h3>")
        parts.append(table(["کیف بیرونی", "نقش", "تعداد اعضا", "مجموع", "اعضا و مبالغ"], rows))
        parts.append(
            '<p class="note">این کیف‌ها برچسب صرافی ندارند ولی ممکن است سرویس یا صراف غیررسمی باشند؛ پیشنهاد می‌شود جداگانه بررسی شوند.</p>'
        )
    parts.append("</section>")
    return "".join(parts)


def section_method(ctx: _Ctx, spot_checks: list[str], number: str = "۶.") -> str:
    inv = ctx.inv
    token = escape(inv.token)
    rows = []
    for p in inv.focus:
        v = p.verification
        if v is None:
            continue
        status = {"verified": ("ok", "✓ مطابق"), "mismatch": ("bad", "✗ مغایرت"), "unavailable": ("mid", "در دسترس نبود")}[v.status]
        all_rows = "—"
        if v.all_our_count is not None:
            all_rows = f"{count(v.all_our_count)} / {count(v.all_scan_count)}"
            if v.all_only_scan or v.all_only_ours:
                all_rows += "<div>فقط در TronScan: " + ("، ".join(tx_link(h) for h in v.all_only_scan) or "—") + "</div>"
                all_rows += "<div>فقط در دادهٔ ما: " + ("، ".join(tx_link(h) for h in v.all_only_ours) or "—") + "</div>"
        rows.append(
            [
                ctx.chip(p.address),
                f"{count(v.our_count)} / {count(v.scan_count) if v.scan_count is not None else '—'}",
                f"<bdi>{num(v.our_in, 6)}</bdi><br><bdi>{num(v.scan_in, 6)}</bdi>",
                f"<bdi>{num(v.our_out, 6)}</bdi><br><bdi>{num(v.scan_out, 6)}</bdi>",
                f'<span class="pill {status[0]}">{status[1]}</span>'
                + (f'<div class="en" dir="ltr">{escape(v.note)}</div>' if v.note else "")
                + (f"<div>فقط در دادهٔ ما: {count(len(v.only_ours))} · فقط در TronScan: {count(len(v.only_scan))}</div>" if v.status == "mismatch" else ""),
                all_rows,
            ]
        )
    limits = [
        "تحلیل زنجیره‌ای فقط جابه‌جایی پول بین آدرس‌ها را نشان می‌دهد؛ این‌که چه کسی پشت هر آدرس است از خودِ بلاکچین به دست نمی‌آید و باید با مدارک دیگر (اقرار، اطلاعات صرافی و …) تکمیل شود.",
        "کیف صرافی و سرویس پول مشتریان زیادی را با هم جابه‌جا می‌کند؛ انتقال از/به چنین کیفی یا داشتن صرافی مشترک، شاهد ضعیفی برای ارتباط دو شخص است.",
        "برچسب صرافی‌ها از فهرست داخلی ChainTrace و برچسب‌های عمومی TronScan گرفته شده و ممکن است کامل یا کاملاً دقیق نباشد؛ کیف بدون برچسب لزوماً کیف شخصی نیست.",
        "مسیرهای غیرمستقیم (از طریق کیف واسط) فقط وقتی «همان پول» محسوب می‌شوند که مبلغ و زمان ورود و خروج با هم جور باشد؛ در غیر این صورت صرفاً وجود طرف‌حساب مشترک را نشان می‌دهند.",
        "ارزش دلاری بر پایهٔ قیمت روز انتقال است (USDT معادل یک دلار؛ TRX با قیمت روزانهٔ CoinGecko/Binance). زمان‌ها به وقت جهانی (UTC) هستند.",
    ]
    for m in inv.members:
        if m.truncated and not m.is_focus:
            limits.append(
                f"کیف {ctx.chip(m.address)} بیش از سقف دریافت تراکنش دارد و فقط {count(m.transfer_count)} انتقال اخیر آن بررسی شده؛ "
                "ممکن است انتقال‌های قدیمی‌تر آن با اعضای دیگر در این گزارش نباشد (انتقال‌های آن با کیف‌های اصلی کامل است، چون تاریخچهٔ کیف‌های اصلی کامل دریافت شده)."
            )
    if not ctx.has_toman:
        limits.append(
            "نرخ تومان در زمان تهیهٔ گزارش در دسترس نبود (سرویس‌های Nobitex و Wallex از این شبکه پاسخ ندادند)؛ به همین دلیل ستون‌های تومان حذف شده‌اند."
        )
    elif ctx.manual_toman:
        limits.append(
            f"<b>نرخ تومان به‌صورت دستی تنظیم شده است:</b> هر دلار = {toman(inv.toman.rate)}، تنظیم‌شده در تاریخ {day(inv.generated_at)} "
            "(نرخ USDT/تومان صفحهٔ قیمت نوبیتکس در همان روز). چون نرخ‌های تاریخی در دسترس نبود، <b>همهٔ ارزش‌های تومانی با همین نرخ امروز</b> "
            "محاسبه شده‌اند (ارزش دلاری روز انتقال × نرخ امروز)، نه با نرخ روز انتقال؛ برای انتقال‌های سال‌های ۱۴۰۰ تا ۱۴۰۲ این عدد از ارزش تومانی آن روز بیشتر است."
        )
    focus_counts = "؛ ".join(
        f"{ctx.chip(p.address)}: {count(p.transfer_count)} انتقال سالم" + (f" و {count(p.spam_count)} هرزنامهٔ حذف‌شده" if p.spam_count else "")
        for p in inv.focus
    )
    parts = [
        f'<section id="method"><h2>{number} روش کار، منابع داده و محدودیت‌ها</h2>',
        "<h3>روش کار</h3><ul>",
        f"<li>تاریخچهٔ <b>کامل</b> هر کیف اصلی از بلاکچین Tron دریافت شد ({focus_counts}). برای کیف‌های دیگر فهرست تازه‌ترین انتقال‌ها تا سقف مشخص دریافت شد.</li>",
        "<li>انتقال‌های ناموفق، انتقال با مبلغ صفر و توکن‌های جعلی/تبلیغاتی (مثلاً توکنی با نام USDT ولی قرارداد متفاوت) کنار گذاشته شدند.</li>",
        f"<li>تمرکز تحلیل روی {token} (قرارداد رسمی {mono(inv.token_contract or '-')}) است؛ جمع TRX نیز در پروفایل هر کیف آمده است.</li>",
        "<li>انتقال‌های مستقیم بین اعضا، کیف‌های واسط مشترک و مسیرهای چندمرحله‌ای با موتور «ارتباط کیف‌ها»ی ChainTrace استخراج شد.</li>",
        "<li>الگوهای رفتاری (کیف عبوری، جمع‌آوری از فرستنده‌های متعدد و …) قاعده‌محور هستند و فقط برای جلب توجه تحلیل‌گرند، نه حکم قطعی.</li>",
        "</ul><h3>منابع داده</h3><ul>",
        "<li><b>TronGrid</b> (API رسمی شبکهٔ Tron): تاریخچهٔ انتقال‌ها و موجودی‌ها.</li>",
        "<li><b>TronScan</b>: منبع مستقل دوم برای راستی‌آزمایی اعداد، و برچسب عمومی آدرس‌ها (نام صرافی‌ها).</li>",
        "<li><b>قرارداد USDT</b>: وضعیت مسدودی هر آدرس (تابع isBlackListed).</li>",
        "<li><b>CoinGecko / Binance</b>: قیمت روزانهٔ TRX؛ <b>Nobitex</b>: نرخ روزانهٔ تومان (در صورت دسترسی).</li>",
        '<li><b>گزارش‌های عمومی حملهٔ ۲۸ خرداد ۱۴۰۴ (18 June 2025) به صرافی نوبیتکس</b>: مهاجمان دارایی کیف‌های نوبیتکس را به آدرس‌های «سوزاندن» '
        '(از جمله <bdi class="mono">TKFuckiRGCTerroristsNoBiTEXy2r7mNX</bdi>) فرستادند؛ این آدرس در فهرست داخلی برچسب خورده و هر کیفی که آن روز به آن پول فرستاده '
        'کیف تحت کنترل نوبیتکس بوده است. منابع: <a href="https://www.scorechain.com/blog/nobitex-hack" target="_blank" rel="noopener">Scorechain</a>، '
        '<a href="https://fortune.com/crypto/2025/06/18/nobitex-gonjeshke-darande-predatory-sparrow-iran-israel-hack/" target="_blank" rel="noopener">Fortune</a>.</li>',
        "</ul><h3>راستی‌آزمایی با TronScan</h3>",
        f"<p>برای هر کیف اصلی، همهٔ انتقال‌های {token} یک‌بار دیگر از TronScan گرفته و تک‌به‌تک (شناسهٔ تراکنش، فرستنده، گیرنده، مبلغ) با دادهٔ این گزارش مقایسه شد.</p>",
        table(
            ["کیف", f"تعداد انتقال {token} (ما / TronScan)", "جمع دریافتی (ما، TronScan)", "جمع ارسالی (ما، TronScan)", "نتیجه",
             "همهٔ ردیف‌های TRC20 با هر توکن (ما / API ترون‌اسکن)"],
            rows,
        ),
        '<p class="note">ستون آخر همهٔ انتقال‌های TRC20 را با هر توکنی (حتی هرزنامه) می‌شمارد و مقایسه را تک‌به‌تک با API ترون‌اسکن انجام می‌دهد؛ '
        "شناسهٔ هر ردیفی که فقط در یک طرف باشد همان‌جا آمده است. شمارندهٔ صفحهٔ وب tronscan.org گاهی با API همان سایت یک عدد اختلاف دارد؛ ملاک، مقایسهٔ تک‌به‌تک است.</p>",
    ]
    if spot_checks:
        parts.append("<h3>بررسی دستی نمونه‌ها</h3><ul>" + "".join(f"<li>{c}</li>" for c in spot_checks) + "</ul>")
    parts.append("<h3>محدودیت‌ها و نکات احتیاطی</h3><ul>" + "".join(f"<li>{x}</li>" for x in limits) + "</ul></section>")
    return "".join(parts)


def section_appendix(ctx: _Ctx, files: list[str], number: str = "۷.") -> str:
    inv = ctx.inv
    rows = [[ctx.chip(m.address), ctx.full(m.address), "✓" if m.is_focus else ""] for m in inv.members]
    labelled = sorted(
        (a for a in inv.labels if a not in ctx.index), key=lambda a: inv.labels[a].lower()
    )
    lab_rows = [
        [
            ctx.full(a),
            f"<bdi>{escape(inv.labels[a])}</bdi>",
            {"tronscan": "TronScan", "builtin": "فهرست داخلی (تأییدنشده)", "user": "کاربر"}.get(inv.label_sources.get(a, ""), "—"),
        ]
        for a in labelled
    ]
    glossary = [
        ("کیف اصلی", "کیفی که مالک آن اقرار کرده و محور این گزارش است."),
        ("طرف‌حساب", "هر آدرسی که با کیف مورد نظر انتقال داشته است."),
        ("کیف واسط", "آدرسی بیرون از فهرست که از یک عضو پول گرفته و به عضو دیگر پول داده است."),
        ("کیف عبوری (Pass-through)", "کیفی که تقریباً همهٔ دریافتی خود را کمی بعد می‌فرستد و موجودی نگه نمی‌دارد."),
        ("صرافی/سرویس", "کیفی که پول مشتریان زیادی را جابه‌جا می‌کند (صدها طرف‌حساب یا برچسب صرافی)."),
        ("حساب قرارداد هوشمند", "آدرسی که با کد اداره می‌شود، نه با کلید خصوصی یک شخص؛ معمولاً آدرس واریز ساخته‌شده توسط یک سرویس."),
    ]
    return (
        f'<section id="appendix"><h2>{number} پیوست</h2><h3>فهرست کامل آدرس‌ها</h3>'
        + table(["#", "آدرس", "اصلی"], rows)
        + ("<h3>برچسب آدرس‌های بیرونی ذکرشده در گزارش</h3>" + table(["آدرس", "برچسب", "منبع"], lab_rows) if lab_rows else "")
        + "<h3>واژه‌نامه</h3><dl>"
        + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in glossary)
        + '</dl><h3 class="no-print">فایل‌های داده</h3><ul class="files no-print">'
        + "".join(f'<li><a href="data/{escape(f)}" download><bdi>{escape(f)}</bdi></a></li>' for f in files)
        + "</ul></section>"
    )


# --- page -------------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f7f9;--surface:#fff;--surface-2:#f1f3f6;--border:#e3e6eb;--text:#0f172a;--text-2:#475569;--muted:#8a93a3;
--accent:#2a78d6;--accent-soft:#e6f0fc;--f0:#2a78d6;--f1:#eb6834;--f2:#1baf7a;--f3:#4a3aa7;--f4:#e87ba4;--svc:#4a3aa7;
--good:#0a7d0a;--good-bg:#e5f5e5;--warn:#9a6700;--warn-bg:#fff4d6;--bad:#b42323;--bad-bg:#fde8e8;--shadow:0 1px 2px rgba(15,23,42,.06)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0b0f17;--surface:#121824;--surface-2:#1a2130;--border:#263044;
--text:#f1f5f9;--text-2:#b6c0cf;--muted:#7d8799;--accent:#5b9eee;--accent-soft:#16263d;--f0:#3987e5;--f1:#e8713f;--f2:#1fb882;
--f3:#9085e9;--f4:#d55181;--svc:#9085e9;--good:#4cc04c;--good-bg:#12301a;--warn:#f2b632;--warn-bg:#33290f;--bad:#f07878;--bad-bg:#3a1717;--shadow:none}}
:root[data-theme=dark]{--bg:#0b0f17;--surface:#121824;--surface-2:#1a2130;--border:#263044;--text:#f1f5f9;--text-2:#b6c0cf;
--muted:#7d8799;--accent:#5b9eee;--accent-soft:#16263d;--f0:#3987e5;--f1:#e8713f;--f2:#1fb882;--f3:#9085e9;--f4:#d55181;--svc:#9085e9;
--good:#4cc04c;--good-bg:#12301a;--warn:#f2b632;--warn-bg:#33290f;--bad:#f07878;--bad-bg:#3a1717;--shadow:none}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:Vazirmatn,system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif;
font-size:15px;line-height:1.9;overflow-wrap:anywhere}
main{max-width:1120px;margin:0 auto;padding:16px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:30px;line-height:1.5;margin:0 0 8px}h2{font-size:21px;margin:0 0 14px;padding-bottom:8px;border-bottom:2px solid var(--border);line-height:1.6}
h3{font-size:16.5px;margin:26px 0 8px}h4{font-size:15.5px;margin:0 0 6px}
section{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:22px;margin:16px 0;box-shadow:var(--shadow)}
.cover{padding:34px 26px;border-top:5px solid var(--accent)}.cover .sub{color:var(--text-2);font-size:16px;margin:0 0 18px}
.meta{display:flex;flex-wrap:wrap;gap:8px 26px;color:var(--text-2);font-size:14px;margin:14px 0}
.meta b{color:var(--text)}
.keys{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin-top:16px}
.key{border:1px solid var(--border);border-radius:10px;padding:10px 12px;background:var(--surface-2);border-inline-start:5px solid var(--c)}
.key .mono{font-size:12.5px}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 8px;margin-top:18px}
nav.toc a{background:var(--accent-soft);padding:3px 12px;border-radius:999px;font-size:13.5px}
.mono{font-family:Consolas,"Cascadia Mono","Courier New",monospace;direction:ltr;unicode-bidi:isolate;font-size:.9em}
bdi.greg{color:var(--muted);font-size:.86em;direction:ltr;margin-inline-start:4px;white-space:nowrap}
.date{white-space:nowrap}.amt{white-space:nowrap}.tok{font-size:.82em;color:var(--text-2)}
.chip{display:inline-block;min-width:30px;text-align:center;padding:0 7px;border-radius:7px;background:var(--surface-2);
border:1px solid var(--border);color:var(--text);font-weight:700;font-size:.88em;line-height:1.75;direction:ltr}
.chip.f0{background:var(--f0)}.chip.f1{background:var(--f1)}.chip.f2{background:var(--f2)}.chip.f3{background:var(--f3)}.chip.f4{background:var(--f4)}
.chip.f0,.chip.f1,.chip.f2,.chip.f3,.chip.f4{color:#fff;border-color:transparent}
.chip.svc{border-color:var(--svc);color:var(--svc)}a.chip:hover{text-decoration:none;filter:brightness(1.08)}
.tag{display:inline-block;background:var(--warn-bg);color:var(--warn);border-radius:6px;padding:0 6px;font-size:.8em;line-height:1.7}
.pill{display:inline-block;border-radius:999px;padding:0 10px;font-size:.82em;line-height:1.8;white-space:nowrap;background:var(--surface-2);color:var(--text-2)}
.pill.ok{background:var(--good-bg);color:var(--good);font-weight:700}.pill.bad,.pill.sev-high,.pill.sev-critical{background:var(--bad-bg);color:var(--bad)}
.pill.mid,.pill.sev-medium{background:var(--warn-bg);color:var(--warn)}.pill.weak,.pill.sev-info,.pill.sev-low{background:var(--surface-2);color:var(--text-2)}
.summary{margin:0;padding:0;list-style:none;counter-reset:s}
.summary li{counter-increment:s;position:relative;padding:9px 46px 9px 0;border-bottom:1px solid var(--border)}
.summary li:last-child{border-bottom:0}
.summary li::before{content:counter(s,persian);position:absolute;inset-inline-start:0;top:10px;width:30px;height:30px;border-radius:50%;
background:var(--accent-soft);color:var(--accent);font-weight:700;text-align:center;line-height:30px}
.tbl{overflow-x:auto;border:1px solid var(--border);border-radius:10px;margin:8px 0 6px;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:max-content;min-width:100%;font-size:13.5px}
th,td{padding:7px 10px;text-align:start;vertical-align:top;border-bottom:1px solid var(--border);overflow-wrap:normal;max-width:360px}
th{background:var(--surface-2);font-weight:700;white-space:nowrap;color:var(--text-2);font-size:12.5px}
tbody tr:last-child td{border-bottom:0}tbody tr:nth-child(even){background:color-mix(in srgb,var(--surface-2) 45%,transparent)}
figure{margin:18px 0;padding:0}figure img{display:block;width:100%;height:auto;background:#fff;border:1px solid var(--border);border-radius:10px}
figcaption{color:var(--text-2);font-size:13.5px;margin-top:8px;line-height:1.85}
.dl{display:inline-flex;gap:6px;margin-inline-start:10px;vertical-align:middle}
.dl a{border:1px solid var(--border);border-radius:7px;padding:0 9px;font-size:12px;background:var(--surface-2);white-space:nowrap}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:12px 0}
.card{border:1px solid var(--border);border-radius:10px;padding:10px 12px;background:var(--surface-2)}
.cards.f0 .card{border-top:3px solid var(--f0)}.cards.f1 .card{border-top:3px solid var(--f1)}.cards.f2 .card{border-top:3px solid var(--f2)}
.cards.f3 .card{border-top:3px solid var(--f3)}.cards.f4 .card{border-top:3px solid var(--f4)}
.card .k{color:var(--text-2);font-size:12.5px}.card .v{font-size:19px;font-weight:700;line-height:1.7}.card .s{color:var(--text-2);font-size:12.5px;line-height:1.7}
.note{color:var(--text-2);font-size:13.5px;background:var(--surface-2);border-radius:8px;padding:8px 12px;margin:8px 0}
.note.warn{background:var(--warn-bg);color:var(--text);border-inline-start:4px solid var(--warn)}
.muted{color:var(--muted)}.addr{margin:0 0 6px}
.findings{padding:0;margin:8px 0;list-style:none}.findings li{padding:8px 0;border-bottom:1px solid var(--border)}.findings li:last-child{border:0}
.en{color:var(--muted);font-size:12.5px;direction:ltr;text-align:left;font-family:"Segoe UI",system-ui,sans-serif;line-height:1.6;margin-top:2px}
.box{border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin:10px 0}
.exit-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin-bottom:4px}.exit-head .sum{margin-inline-start:auto;font-weight:700}
.exit ul{margin:4px 0}.exit li{margin:4px 0}
dl{margin:8px 0}dt{font-weight:700;margin-top:8px}dd{margin:0;color:var(--text-2)}
ul{padding-inline-start:22px;margin:8px 0}.files{columns:2}
footer{color:var(--muted);font-size:12.5px;text-align:center;padding:14px 0 30px}
.bar{position:sticky;top:0;z-index:5;display:flex;justify-content:space-between;align-items:center;gap:10px;background:color-mix(in srgb,var(--bg) 88%,transparent);
backdrop-filter:blur(6px);padding:8px 16px;border-bottom:1px solid var(--border);font-size:13px;color:var(--text-2)}
.bar button{font:inherit;font-size:13px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:8px;padding:2px 12px;cursor:pointer}
.bar .btns{display:flex;gap:6px}
@media (max-width:640px){body{font-size:14px}main{padding:10px}section{padding:14px;border-radius:12px}h1{font-size:23px}h2{font-size:18.5px}
.cover{padding:22px 16px}.files{columns:1}.summary li{padding-inline-start:40px}table{font-size:12.5px}th,td{padding:6px 8px}.dl{display:flex;margin:6px 0 0}}
@page{size:A4;margin:14mm 12mm}
@media print{:root{--bg:#fff;--surface:#fff;--surface-2:#f3f4f6;--border:#cfd4dc;--text:#000;--text-2:#333;--muted:#666;--accent:#1c5cab;--accent-soft:#e6f0fc;
--f0:#2a78d6;--f1:#eb6834;--f2:#1baf7a;--f3:#4a3aa7;--f4:#e87ba4;--good-bg:#e5f5e5;--good:#0a7d0a;--warn-bg:#fff4d6;--warn:#7a5200;--bad-bg:#fde8e8;--bad:#b42323}
body{font-size:10.5pt;line-height:1.75}main{max-width:none;padding:0}.no-print,.bar{display:none!important}
section{border:0;box-shadow:none;padding:0;margin:0 0 6mm;border-radius:0;break-before:page}section.cover,section#summary{break-before:auto}
.cover{border-top:4px solid var(--accent);padding-top:8mm}h2,h3,h4{break-after:avoid}figure,.card,.box,tr,.summary li{break-inside:avoid}
.tbl{overflow:visible;border-radius:0}table{font-size:8.5pt}th,td{padding:3px 5px}a{color:inherit}.chip,.pill,.tag,th,.card,.note{-webkit-print-color-adjust:exact;print-color-adjust:exact}
figure img{max-height:235mm;width:auto;max-width:100%;margin:0 auto;border:0}}
"""

SCRIPT = """
(function(){var r=document.documentElement,k='chaintrace-report-theme';
try{var s=localStorage.getItem(k);if(s)r.setAttribute('data-theme',s)}catch(e){}
var t=document.getElementById('theme');if(t)t.onclick=function(){
var d=r.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
d=d==='dark'?'light':'dark';r.setAttribute('data-theme',d);try{localStorage.setItem(k,d)}catch(e){}};
var p=document.getElementById('print');if(p)p.onclick=function(){window.print()};})();
"""


def _fonts() -> str:
    """Vazirmatn embedded from the dashboard build, so the page looks right offline; else system fonts."""
    faces = []
    for subset, unicode_range in (
        ("arabic", "U+0600-06FF,U+0750-077F,U+0870-088E,U+0890-0891,U+0898-08E1,U+08E3-08FF,U+200C-200E,U+2010-2011,U+204F,U+2E41,U+FB50-FDFF,U+FE70-FE74,U+FE76-FEFC"),
        ("latin", "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"),
    ):  # fmt: skip
        for weight in (400, 700):
            found = sorted(FONT_DIR.glob(f"vazirmatn-{subset}-{weight}-normal-*.woff2")) if FONT_DIR.is_dir() else []
            if not found:
                continue
            data = base64.b64encode(found[0].read_bytes()).decode("ascii")
            faces.append(
                f"@font-face{{font-family:Vazirmatn;font-style:normal;font-weight:{weight};font-display:swap;"
                f"src:url(data:font/woff2;base64,{data}) format('woff2');unicode-range:{unicode_range}}}"
            )
    return "".join(faces)


def render_html(
    inv: Investigation,
    graphs: list[GraphFile],
    data_files: list[str],
    title: str | None = None,
    case_date: str | None = None,
    spot_checks: list[str] | None = None,
) -> str:
    ctx = _Ctx(inv)
    by_key = {g.key: g for g in graphs}
    title = title or DEFAULT_TITLE
    counter = iter(range(1, 100))

    def fig_no() -> int:
        return next(counter)

    token = escape(inv.token)
    now = inv.generated_at.astimezone(timezone.utc)
    keys = "".join(
        f'<div class="key" style="--c:var(--f{i % len(FOCUS_COLORS)})">کیف اصلی {ctx.chip(p.address)}<br>{ctx.full(p.address)}</div>'
        for i, p in enumerate(inv.focus)
    )
    toc = [
        ("summary", "خلاصهٔ مدیریتی"),
        ("exits", "مسیر خروج و ورود پول"),
        ("connections", "ارتباط کیف‌های اصلی"),
        ("timeline", "روند زمانی"),
        *[(f"wallet-{p.index}", f"کیف #{p.index}") for p in inv.focus],
        ("list", "کل فهرست"),
        ("method", "روش و راستی‌آزمایی"),
        ("appendix", "پیوست"),
    ]
    cover = (
        '<section class="cover" id="cover">'
        f"<h1>{escape(title)}</h1>"
        f'<p class="sub">بررسی {count(len(inv.focus))} کیف اصلی در فهرست {count(len(inv.members))} کیف‌پول شبکهٔ '
        f"<bdi>{escape(inv.chain.value.capitalize())}</bdi> با تمرکز بر <bdi>{token}</bdi></p>"
        '<div class="meta">'
        + (f"<span>تاریخ پرونده: <b>{escape(case_date)}</b></span>" if case_date else "")
        + f"<span>تاریخ تهیهٔ گزارش: <b>{jalali(now)}</b> <bdi class='greg'>{now:%Y-%m-%d %H:%M} UTC</bdi></span>"
        "<span>تهیه‌شده با: <b>ChainTrace</b></span>"
        "</div>"
        f'<div class="keys">{keys}</div>'
        '<nav class="toc no-print">' + "".join(f'<a href="#{k}">{escape(v)}</a>' for k, v in toc) + "</nav>"
        "</section>"
    )
    summary = (
        '<section id="summary"><h2>۱. خلاصهٔ مدیریتی</h2><ol class="summary">'
        + "".join(f"<li>{s}</li>" for s in summary_sentences(ctx))
        + "</ol>"
        '<p class="note">شماره‌های <bdi>#1</bdi> تا <bdi>#'
        + str(len(inv.members))
        + "</bdi> همان ترتیب فهرست اولیه است و در همهٔ جدول‌ها و نمودارها ثابت می‌ماند. مبالغ به "
        f"<bdi>{token}</bdi> هستند مگر خلافش ذکر شود.</p></section>"
    )
    # Built in reading order, so the figures are numbered as they appear.
    sec = iter(range(2, 20))

    def no() -> str:
        return f"{fa_digits(next(sec))}."

    body = cover + summary + section_exits(ctx, by_key, fig_no, no())
    body += section_connections(ctx, by_key, fig_no, no()) + section_timeline(ctx, by_key, fig_no, no())
    profile_no = fa_digits(next(sec))
    for i, p in enumerate(inv.focus, start=1):
        body += section_profile(ctx, p, by_key, fig_no, f"{profile_no}-{fa_digits(i)}.")
    body += section_list(ctx, by_key, fig_no, no()) + section_method(ctx, spot_checks or [], no()) + section_appendix(ctx, data_files, no())
    return (
        '<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_fonts()}{CSS}</style></head><body>"
        '<div class="bar no-print"><span>ChainTrace · گزارش تحقیق</span><span class="btns">'
        '<button id="theme" type="button">روشن / تیره</button><button id="print" type="button">چاپ / PDF</button></span></div>'
        f"<main>{body}<footer>این گزارش به‌صورت خودکار از داده‌های بلاکچین ساخته شده است. ردیابی مبلغ و امتیاز ریسک مبتنی بر مدل و "
        "احتمالاتی هستند و باید با داده‌های بلاکچین و مدارک دیگر بررسی شوند. · ChainTrace</footer></main>"
        f"<script>{SCRIPT}</script></body></html>"
    )


# --- data exports ------------------------------------------------------------------------------------------


def _csv(path: Path, head: list[str], rows: list[list]) -> None:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(head)
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")  # BOM: Excel reads Persian correctly


def _plain(value) -> str:
    return "" if value is None else format(value, "f") if isinstance(value, Decimal) else str(value)


def write_data(inv: Investigation, out_dir: Path) -> list[str]:
    """CSV (UTF-8 with BOM) and JSON exports; returns the file names."""
    out_dir.mkdir(parents=True, exist_ok=True)
    index = {m.address: m.index for m in inv.members}
    files: list[str] = []

    def label(address: str) -> str:
        return inv.labels.get(address, "")

    head = [
        "تاریخ (UTC)", "تاریخ شمسی", "جهت", "طرف‌حساب", "شمارهٔ طرف‌حساب در فهرست", "برچسب طرف‌حساب", "مبلغ", "توکن",
        "ارزش دلاری روز", "ارزش تومانی روز", "شناسهٔ تراکنش", "لینک TronScan",
    ]  # fmt: skip
    for p in inv.focus:
        rows = []
        for t in inv.focus_history.get(p.address, []):
            incoming = t.to_address == p.address
            other = t.from_address if incoming else t.to_address
            rows.append(
                [
                    f"{t.timestamp:%Y-%m-%d %H:%M:%S}", jalali(t.timestamp, month_name=False), "ورودی" if incoming else "خروجی",
                    other, index.get(other, ""), label(other), _plain(t.amount), t.token_symbol, _plain(t.usd_then),
                    _plain(t.toman_then), t.tx_hash, TX_URL.format(t.tx_hash),
                ]
            )  # fmt: skip
        name = f"transfers_{p.index:02d}_{p.address}.csv"
        _csv(out_dir / name, head, rows)
        files.append(name)

    head = [
        "تاریخ (UTC)", "تاریخ شمسی", "از (شماره)", "از (آدرس)", "به (شماره)", "به (آدرس)", "مبلغ", "توکن", "ارزش دلاری روز",
        "ارزش تومانی روز", "شناسهٔ تراکنش", "لینک TronScan",
    ]  # fmt: skip
    rows = [
        [
            f"{t.timestamp:%Y-%m-%d %H:%M:%S}", jalali(t.timestamp, month_name=False), t.from_index, t.from_address, t.to_index,
            t.to_address, _plain(t.amount), t.token_symbol, _plain(t.usd_then), _plain(t.toman_then), t.tx_hash,
            TX_URL.format(t.tx_hash),
        ]
        for t in inv.member_transfers
    ]  # fmt: skip
    _csv(out_dir / "transfers_between_members.csv", head, rows)
    files.append("transfers_between_members.csv")

    names = [f"#{m.index} {m.address}" for m in inv.members]
    rows = [[names[i], *[_plain(v) for v in row], _plain(sum(row, Decimal(0)))] for i, row in enumerate(inv.matrix)]
    rows.append(["جمع دریافتی", *[_plain(sum((r[j] for r in inv.matrix), Decimal(0))) for j in range(len(names))], ""])
    _csv(out_dir / "matrix.csv", [f"فرستنده \\ گیرنده ({inv.token})", *names, "جمع ارسالی"], rows)
    files.append("matrix.csv")

    head = [
        "رتبه", "شماره", "آدرس", "برچسب", "کیف اصلی", "رفتار صرافی/سرویس", f"ارسال به اعضا ({inv.token})",
        f"دریافت از اعضا ({inv.token})", "مجموع", "تعداد انتقال با اعضا", "تعداد اعضای مرتبط", "کل انتقال‌ها", "طرف‌حساب‌ها",
        "مسدودی Tether",
    ]  # fmt: skip
    rows = [
        [
            i, r.index, r.address, label(r.address), "بله" if r.is_focus else "", "بله" if r.likely_service else "",
            _plain(r.sent_to_members), _plain(r.received_from_members), _plain(r.total_with_members), r.transfers_with_members,
            r.partners, r.transfer_count, r.counterparty_count, {True: "مسدود", False: "خیر", None: "نامشخص"}[r.usdt_frozen],
        ]
        for i, r in enumerate(inv.ranking, start=1)
    ]  # fmt: skip
    _csv(out_dir / "ranking.csv", head, rows)
    files.append("ranking.csv")

    (out_dir / "investigation.json").write_text(
        json.dumps(json.loads(inv.model_dump_json()), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    files.append("investigation.json")
    return files


def write_report(
    inv: Investigation,
    out_dir: Path,
    title: str | None = None,
    case_date: str | None = None,
    spot_checks: list[str] | None = None,
    archive: Path | None = None,
) -> Path:
    """Write report.html, graphs/ and data/ into `out_dir` and zip them; returns the ZIP path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    graphs = render_all(inv, out_dir / "graphs")
    data_files = write_data(inv, out_dir / "data")
    html = render_html(inv, graphs, data_files, title=title, case_date=case_date, spot_checks=spot_checks)
    (out_dir / "report.html").write_text(html, encoding="utf-8")
    archive = Path(archive) if archive else out_dir.parent / f"ChainTrace-Report-{out_dir.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(out_dir / "report.html", "report.html")
        for sub in ("graphs", "data"):
            for path in sorted((out_dir / sub).iterdir()):
                if path.is_file():
                    z.write(path, f"{sub}/{path.name}")
    return archive
