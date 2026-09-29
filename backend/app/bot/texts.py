"""Telegram message formatting (HTML parse mode), kept free of Telegram imports."""

from decimal import Decimal
from html import escape

from app.models import Counterparty, WalletOverview
from app.services.labels import LabelService
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

HELP = (
    "<b>ChainTrace</b> — ردیابی تراکنش‌های کیف پول\n\n"
    "یک آدرس بفرستید، یا:\n"
    "/wallet <code>آدرس</code> [شبکه] — خلاصه، ریسک و طرف‌حساب‌ها\n"
    "/graph <code>آدرس</code> — تصویر گراف جریان پول\n"
    "/risk <code>آدرس</code> — گزارش ریسک\n"
    "/trace <code>آدرس</code> <code>هش_تراکنش</code> [مبلغ] — ردیابی مسیر پول\n"
    "/watch <code>آدرس</code> [حداقل_مبلغ] [توکن] — هشدار تراکنش جدید\n"
    "/unwatch <code>آدرس</code> — حذف از واچ‌لیست\n"
    "/watchlist — لیست آدرس‌های تحت نظر\n\n"
    "شبکه‌ها: tron, ethereum, bsc, polygon, arbitrum, optimism, base, avalanche, bitcoin, solana"
)


def num(value: Decimal | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1:
        return f"{value.quantize(Decimal('0.01')):,}"
    return f"{value.normalize():f}"


def code(address: str) -> str:
    return f"<code>{escape(address)}</code>"


def overview_text(
    o: WalletOverview,
    risk: RiskReport | None,
    cps: list[Counterparty],
    labels: LabelService,
    dashboard_url: str | None = None,
) -> str:
    lines = [f"<b>{o.chain.value.upper()}</b> {code(o.address)}"]
    own = labels.get(o.chain, o.address)
    if own:
        lines.append(f"🏷 {escape(own.name)} ({own.category.value})")
    if o.total_usd is not None:
        lines.append(f"💰 ارزش کل: <b>${num(o.total_usd)}</b>")
    for b in o.balances[:6]:
        lines.append(f"  • {num(b.amount)} {escape(b.token_symbol)}")
    first = o.first_seen.strftime("%Y-%m-%d") if o.first_seen else "—"
    last = o.last_seen.strftime("%Y-%m-%d") if o.last_seen else "—"
    lines.append(f"📅 {first} ← → {last} · {o.transfer_count} انتقال · {o.counterparty_count} طرف‌حساب")
    if o.truncated:
        lines.append("⚠️ تاریخچه کامل دریافت نشد (سقف)")
    if risk is not None:
        lines.append(f"\n{LEVEL_ICON.get(risk.level, '')} ریسک: <b>{risk.score}/100</b> ({risk.level})")
        for f in risk.findings[:4]:
            lines.append(f"  • {escape(f.title)}")

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
        lines.append(f"\n<b>[{f.severity.value}] {escape(f.title)}</b>\n{escape(f.detail)}")
        for e in f.evidence[:3]:
            lines.append(f"  {code(e)}")
    return "\n".join(lines)


def trace_text(t: TraceResult) -> str:
    direction = "کجا رفت" if t.direction.value == "forward" else "از کجا آمد"
    lines = [
        f"🔎 ردیابی ({direction}) {num(t.traced_amount)} {escape(t.token_symbol)}",
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


def alert_text(a: Alert) -> str:
    arrow = "⬅️ دریافت" if a.direction.value == "in" else "➡️ ارسال"
    name = f" ({escape(a.watch_name)})" if a.watch_name else ""
    return (
        f"🔔 <b>تراکنش جدید</b>{name}\n{a.chain.value.upper()} {code(a.address)}\n"
        f"{arrow}: <b>{num(a.amount)} {escape(a.token_symbol)}</b>\n"
        f"طرف‌حساب: {code(a.counterparty)}\n"
        f"tx: {code(a.tx_hash)}\n{a.timestamp.strftime('%Y-%m-%d %H:%M')} UTC"
    )


def parse_args(text: str | None) -> list[str]:
    return (text or "").split()
