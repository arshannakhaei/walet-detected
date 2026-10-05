"""Telegram message formatting (HTML parse mode), kept free of Telegram imports."""

from decimal import Decimal
from html import escape

from app.models import Counterparty, WalletOverview
from app.services.labels import LabelService
from app.services.links import LinkReport
from app.services.monitor import Alert
from app.services.risk import RiskReport
from app.services.tracer import TraceResult

LEVEL_ICON = {"low": "🟢", "medium": "🟠", "high": "🔴", "critical": "🟣"}
REASON_FA = {
    "unspent": "مانده در ولت",
    "unknown_source": "منبع نامشخص",
    "labeled": "رسید به صرافی/سرویس",
    "hub": "ولت پرتراکنش (احتمالاً صرافی)",
    "max_hops": "سقف تعداد مرحله",
    "below_min": "خردشده (کمتر از حداقل)",
    "pruned": "شاخه‌های کوچک حذف‌شده",
    "step_limit": "سقف جستجو",
    "loop": "حلقه",
}

# Persian titles per risk finding code (kept in sync with frontend/src/lib/findings.ts).
FINDING_FA = {
    "flagged_address": "خود آدرس برچسب پرریسک دارد",
    "direct_exposure": "تراکنش مستقیم با آدرس‌های پرریسک",
    "indirect_exposure": "ارتباط غیرمستقیم (۲ لایه) با آدرس‌های پرریسک",
    "exchange_exposure": "تراکنش با صرافی‌ها",
    "pass_through": "ولت عبوری (Pass-through)",
    "rapid_movement": "جابه‌جایی خیلی سریع پول",
    "short_lived_intermediary": "ولت واسط کوتاه‌عمر",
    "fan_in": "دریافت از تعداد زیادی فرستنده در زمان کوتاه",
    "fan_out": "پخش پول به تعداد زیادی ولت در زمان کوتاه",
    "round_amounts": "بیشتر مبالغ رُند",
    "structuring": "مبالغ درست زیر ۱۰٬۰۰۰",
    "new_high_volume": "ولت جدید با حجم بالا",
    "dormant_reactivated": "فعال‌شدن دوباره‌ی ولت خاموش",
    "poisoning_victim": "احتمال از دست دادن پول با Address Poisoning",
    "poisoning_target": "هدف حمله‌ی Address Poisoning",
    "usdt_frozen": "USDT این کیف توسط Tether مسدود شده",
    "sanctioned_oracle": "آدرس تحریم‌شده (اوراکل Chainalysis)",
    "frozen_counterparty": "تراکنش با کیف‌های مسدودشده توسط Tether",
    "sanctioned_counterparty": "تراکنش با کیف‌های تحریم‌شده",
    "likely_service": "احتمالاً کیف صرافی یا سرویس",
}

HELP = (
    "<b>ChainTrace</b> — ردیابی تراکنش‌های کیف پول\n\n"
    "یک آدرس بفرستید، یا:\n"
    "/wallet <code>آدرس</code> [شبکه] — خلاصه، ریسک و طرف‌حساب‌ها\n"
    "/graph <code>آدرس</code> — تصویر گراف جریان پول\n"
    "/risk <code>آدرس</code> — گزارش ریسک\n"
    "/trace <code>آدرس</code> <code>هش_تراکنش</code> [مبلغ] — ردیابی مسیر پول\n"
    "/watch <code>آدرس</code> [حداقل_مبلغ] [توکن] — هشدار تراکنش جدید\n"
    "/unwatch <code>آدرس</code> — حذف از واچ‌لیست\n"
    "/watchlist — لیست آدرس‌های تحت نظر\n"
    "/links <code>آدرس۱</code> <code>آدرس۲</code> ... — ارتباط بین چند کیف (یا فقط لیست آدرس‌ها را بفرستید)\n\n"
    "شبکه‌ها: tron, ethereum, bsc, polygon, arbitrum, optimism, base, avalanche, bitcoin, solana"
)


def num(value: Decimal | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1:
        return f"{value.quantize(Decimal('0.01')):,}"
    return f"{value.normalize():f}"


def money_plain(usd: Decimal | None, toman: Decimal | None = None) -> str:
    """"$1,234.00 · 74,040,000 تومان" for whatever is known, else ""."""
    parts = []
    if usd is not None:
        parts.append(f"${num(usd)}")
    if toman is not None:
        parts.append(f"{toman.quantize(Decimal(1)):,} تومان")
    return " · ".join(parts)


def money(usd: Decimal | None, toman: Decimal | None = None) -> str:
    """" (≈ $1,234.00 · 74,040,000 تومان)", or "" when nothing is known."""
    text = money_plain(usd, toman)
    return f" (≈ {text})" if text else ""


def code(address: str) -> str:
    return f"<code>{escape(address)}</code>"


def overview_text(
    o: WalletOverview,
    risk: RiskReport | None,
    cps: list[Counterparty],
    labels: LabelService,
    dashboard_url: str | None = None,
    toman_rate: Decimal | None = None,
) -> str:
    def toman(usd: Decimal | None) -> Decimal | None:
        return usd * toman_rate if usd is not None and toman_rate else None

    lines = [f"<b>{o.chain.value.upper()}</b> {code(o.address)}"]
    own = labels.get(o.chain, o.address)
    if own:
        lines.append(f"🏷 {escape(own.name)} ({own.category.value})")
    if o.total_usd is not None:
        tmn = toman(o.total_usd)
        lines.append(f"💰 ارزش کل: <b>${num(o.total_usd)}</b>" + (f" · <b>{tmn.quantize(Decimal(1)):,} تومان</b>" if tmn else ""))
    for b in o.balances[:6]:
        lines.append(f"  • {num(b.amount)} {escape(b.token_symbol)}{money(b.usd_value, toman(b.usd_value))}")
    first = o.first_seen.strftime("%Y-%m-%d") if o.first_seen else "—"
    last = o.last_seen.strftime("%Y-%m-%d") if o.last_seen else "—"
    lines.append(f"📅 {first} ← → {last} · {o.transfer_count} انتقال · {o.counterparty_count} طرف‌حساب")
    if o.truncated:
        lines.append("⚠️ تاریخچه کامل دریافت نشد (سقف)")
    if risk is not None:
        lines.append(f"\n{LEVEL_ICON.get(risk.level, '')} ریسک: <b>{risk.score}/100</b> ({risk.level})")
        for f in risk.findings[:4]:
            lines.append(f"  • {escape(FINDING_FA.get(f.code, f.title))}")

    def cp_line(c: Counterparty, amount: Decimal) -> str:
        label = labels.get(o.chain, c.address)
        tag = f" — {escape(label.name)}" if label else ""
        return f"  • {num(amount)} {escape(c.token_symbol)} {code(c.address)}{tag}"

    senders = sorted((c for c in cps if c.received_from > 0), key=lambda c: c.received_from, reverse=True)[:5]
    receivers = sorted((c for c in cps if c.sent_to > 0), key=lambda c: c.sent_to, reverse=True)[:5]
    if senders:
        lines.append("\n⬅️ <b>بیشترین واریز از:</b>")
        lines.extend(cp_line(c, c.received_from) for c in senders)
    if receivers:
        lines.append("\n➡️ <b>بیشترین ارسال به:</b>")
        lines.extend(cp_line(c, c.sent_to) for c in receivers)
    if dashboard_url:
        lines.append(f"\n🔗 <a href=\"{escape(dashboard_url)}\">نمایش در داشبورد</a>")
    return "\n".join(lines)


def risk_text(r: RiskReport) -> str:
    lines = [f"{LEVEL_ICON.get(r.level, '')} ریسک {code(r.address)}: <b>{r.score}/100</b> ({r.level})"]
    s = r.stats
    if s.median_holding_hours is not None:
        lines.append(f"⏱ میانه زمان نگهداری: {s.median_holding_hours:.1f} ساعت")
    if s.pass_through_ratio is not None:
        lines.append(f"🔁 نسبت خروجی به ورودی ({escape(s.main_token or '')}): {s.pass_through_ratio:.0%}")
    if not r.findings:
        lines.append("موردی یافت نشد.")
    for f in r.findings:
        lines.append(f"\n<b>[{f.severity.value}] {escape(FINDING_FA.get(f.code, f.title))}</b>\n{escape(f.detail)}")
        for e in f.evidence[:3]:
            lines.append(f"  {code(e)}")
    return "\n".join(lines)


def trace_text(t: TraceResult, value=None) -> str:
    """`value`: fx.Value of the traced amount (worth at the transfer time and today)."""
    direction = "کجا رفت" if t.direction.value == "forward" else "از کجا آمد"
    worth = ""
    if value is not None:
        then, now = money_plain(value.usd_then, value.toman_then), money_plain(value.usd_now, value.toman_now)
        worth = "".join(
            f"\n{label}: {text}" for label, text in (("💵 ارزش در زمان تراکنش", then), ("💵 ارزش امروز", now)) if text
        )
    lines = [
        f"🔎 ردیابی ({direction}) {num(t.traced_amount)} {escape(t.token_symbol)}{worth}",
        f"tx: {code(t.start.tx_hash)}",
        f"{len(t.flows)} انتقال در مسیر\n",
        "<b>نتیجه:</b>",
    ]
    for reason, amount in sorted(t.summary.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  • {REASON_FA.get(reason.value, reason.value)}: {num(amount)}")
    lines.append("\n<b>مقصدهای اصلی:</b>")
    for ep in t.endpoints[:8]:
        tag = f" — {escape(ep.label.name)}" if ep.label else ""
        lines.append(
            f"  • {num(ep.amount)} → {code(ep.address)}{tag}\n"
            f"     {REASON_FA.get(ep.reason.value, ep.reason.value)} · اطمینان {float(ep.confidence):.0%}"
        )
    return "\n".join(lines)


def links_text(r: LinkReport, url: str | None = None, values: list | None = None) -> str:
    """`values`: fx.Value of each direct link's total today (in r.direct order), if known."""
    values = values or []
    index = {m.address: m.index for m in r.members}

    def name(address: str) -> str:
        i = index.get(address)
        return f"#{i}" if i else code(address)

    token = escape(r.token or "همه‌ی توکن‌ها")
    linked = sum(1 for m in r.members if m.linked_members)
    lines = [
        f"🕸 <b>ارتباط بین {len(r.members)} کیف</b> ({token})",
        f"{linked} کیف مرتبط · {len(r.direct)} ارتباط مستقیم · {len(r.groups)} گروه",
    ]
    if not r.complete:
        lines.append("⚠️ تاریخچه‌ی بعضی کیف‌ها کامل دریافت نشد؛ ممکن است ارتباط‌هایی دیده نشوند.")
    if r.direct:
        lines.append("\n<b>انتقال مستقیم:</b>")
        for i, d in enumerate(r.direct[:15]):
            biggest = d.transfers[0]
            v = values[i] if i < len(values) else None
            worth = money(v.usd_now, v.toman_now) if v is not None else ""
            lines.append(
                f"• {name(d.from_address)} → {name(d.to_address)}: <b>{num(d.total)} {escape(d.token_symbol)}</b>{worth}"
                f" ({d.count} انتقال؛ بزرگ‌ترین {num(biggest.amount)} در {biggest.timestamp:%Y-%m-%d})"
            )
    strong = [p for p in r.paths if not p.through_service]
    if strong:
        lines.append("\n<b>از طریق کیف واسط:</b>")
        for p in strong[:10]:
            via = " → ".join(code(v) for v in p.via)
            extra = ""
            if p.matched:
                m = p.matched[0]
                extra = f"\n   همان پول: {num(m.incoming.amount)} → {num(m.outgoing.amount)} بعد از {m.delay_minutes:g} دقیقه"
            lines.append(
                f"• {name(p.from_address)} → {via} → {name(p.to_address)}: {num(p.amount_out)} {escape(p.token_symbol)}{extra}"
            )
    if r.shared:
        lines.append("\n<b>طرف‌حساب مشترک:</b>")
        for s in r.shared[:8]:
            role = "منبع مشترک" if s.role.value == "common_source" else "مقصد مشترک"
            tag = f" ({escape(s.label.name)})" if s.label else ""
            members = "، ".join(name(m.address) for m in s.members)
            lines.append(f"• {role}{tag}: {code(s.address)} ← {members}")
    if r.groups:
        lines.append("\n<b>گروه‌ها:</b>")
        for i, g in enumerate(r.groups, 1):
            lines.append(f"• گروه {i}: " + "، ".join(name(a) for a in g))
    if not (r.direct or r.paths or r.shared):
        lines.append("\nهیچ انتقالی بین این کیف‌ها پیدا نشد.")
    lines.append("\n<b>شماره‌ها:</b>")
    for m in r.members:
        err = " ⚠️" if m.error else ""
        lines.append(f"#{m.index} {code(m.address)}{err}")
    if url:
        lines.append(f"\n🔗 {url}")
    return "\n".join(lines)


def alert_text(a: Alert, value=None) -> str:
    arrow = "⬅️ دریافت" if a.direction.value == "in" else "➡️ ارسال"
    name = f" ({escape(a.watch_name)})" if a.watch_name else ""
    return (
        f"🔔 <b>تراکنش جدید</b>{name}\n{a.chain.value.upper()} {code(a.address)}\n"
        f"{arrow}: <b>{num(a.amount)} {escape(a.token_symbol)}</b>"
        f"{money(value.usd_then, value.toman_then) if value is not None else ''}\n"
        f"طرف‌حساب: {code(a.counterparty)}\n"
        f"tx: {code(a.tx_hash)}\n{a.timestamp.strftime('%Y-%m-%d %H:%M')} UTC"
    )


def parse_args(text: str | None) -> list[str]:
    return (text or "").split()
