"""Printable HTML reports for a case or a single wallet.

HTML rather than PDF: browsers render Persian (RTL) text correctly and
"Print → Save as PDF" produces the PDF.
"""

from datetime import datetime, timezone
from decimal import Decimal
from html import escape

from app.models import Chain, Counterparty, WalletOverview
from app.services.cases import Case, ItemKind
from app.services.risk import RiskReport

T = {
    "fa": {
        "dir": "rtl",
        "report": "گزارش تحقیق",
        "wallet_report": "گزارش کیف پول",
        "generated": "تاریخ تهیه",
        "chain": "شبکه",
        "address": "آدرس",
        "balances": "موجودی",
        "flows": "ورودی / خروجی",
        "token": "توکن",
        "in": "ورودی",
        "out": "خروجی",
        "first_seen": "اولین فعالیت",
        "last_seen": "آخرین فعالیت",
        "transfers": "تعداد انتقال",
        "risk": "ریسک",
        "score": "امتیاز",
        "findings": "یافته‌ها",
        "none": "موردی یافت نشد",
        "counterparties": "طرف‌حساب‌های اصلی",
        "received_from": "دریافت از",
        "sent_to": "ارسال به",
        "trace": "ردیابی",
        "traced": "مبلغ ردیابی‌شده",
        "endpoints": "مقصدهای نهایی",
        "reason": "وضعیت",
        "confidence": "اطمینان",
        "amount": "مبلغ",
        "path": "مسیر",
        "from": "از",
        "to": "به",
        "notes": "یادداشت‌ها",
        "truncated": "تاریخچه ناقص است (سقف دریافت)",
        "disclaimer": "ردیابی مبلغ و امتیاز ریسک مبتنی بر مدل و احتمالاتی هستند و باید با داده‌ی بلاکچین بررسی شوند.",
    },
    "en": {
        "dir": "ltr",
        "report": "Investigation report",
        "wallet_report": "Wallet report",
        "generated": "Generated",
        "chain": "Chain",
        "address": "Address",
        "balances": "Balances",
        "flows": "In / out",
        "token": "Token",
        "in": "In",
        "out": "Out",
        "first_seen": "First seen",
        "last_seen": "Last seen",
        "transfers": "Transfers",
        "risk": "Risk",
        "score": "Score",
        "findings": "Findings",
        "none": "None",
        "counterparties": "Main counterparties",
        "received_from": "Received from",
        "sent_to": "Sent to",
        "trace": "Trace",
        "traced": "Traced amount",
        "endpoints": "Where the money ended",
        "reason": "Status",
        "confidence": "Confidence",
        "amount": "Amount",
        "path": "Path",
        "from": "From",
        "to": "To",
        "notes": "Notes",
        "truncated": "History is incomplete (fetch limit reached)",
        "disclaimer": "Amount tracing and risk scores are model-based estimates; verify against on-chain data.",
    },
}

CSS = """
body{font-family:Vazirmatn,Tahoma,'Segoe UI',sans-serif;margin:32px;color:#111;background:#fff;line-height:1.6}
h1{margin:0 0 4px}h2{border-bottom:2px solid #333;padding-bottom:4px;margin-top:32px}
h3{margin:20px 0 8px}.muted{color:#666;font-size:13px}
.mono{font-family:Consolas,monospace;direction:ltr;unicode-bidi:embed;word-break:break-all}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{border:1px solid #ccc;padding:4px 8px;text-align:start;vertical-align:top}th{background:#f2f2f2}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;color:#fff;font-weight:bold}
.low{background:#2e7d32}.medium{background:#ef6c00}.high{background:#c62828}.critical{background:#6a1b9a}
.info{background:#546e7a}.box{border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:12px 0}
@media print{body{margin:12mm}h2{page-break-after:avoid}.box{page-break-inside:avoid}}
"""


def _num(value: Decimal | None) -> str:
    if value is None:
        return "—"
    q = value.quantize(Decimal("0.01")) if abs(value) >= 1 else value.normalize()
    return f"{q:,}"


def _date(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return escape(value[:19].replace("T", " "))
    return value.strftime("%Y-%m-%d %H:%M")


def _mono(value: str | None) -> str:
    return f'<span class="mono">{escape(value or "")}</span>'


def wallet_section(
    lang: str,
    overview: WalletOverview,
    risk: RiskReport | None,
    counterparties: list[Counterparty],
    labels: dict[str, str] | None = None,
) -> str:
    t = T[lang]
    labels = labels or {}
    parts = [
        f"<div class='box'><div>{t['chain']}: <b>{escape(overview.chain.value)}</b></div>",
        f"<div>{t['address']}: {_mono(overview.address)}"
        + (f" — <b>{escape(labels[overview.address])}</b>" if overview.address in labels else "")
        + "</div>",
        f"<div>{t['first_seen']}: {_date(overview.first_seen)} · {t['last_seen']}: {_date(overview.last_seen)}"
        f" · {t['transfers']}: {overview.transfer_count}</div>",
    ]
    if overview.truncated:
        parts.append(f"<div class='muted'>⚠ {t['truncated']}</div>")
    parts.append("</div>")

    parts.append(f"<h3>{t['balances']}</h3><table><tr><th>{t['token']}</th><th>{t['amount']}</th><th>USD</th></tr>")
    for b in overview.balances:
        parts.append(f"<tr><td>{escape(b.token_symbol)}</td><td>{_num(b.amount)}</td><td>{_num(b.usd_value)}</td></tr>")
    parts.append("</table>")

    parts.append(f"<h3>{t['flows']}</h3><table><tr><th>{t['token']}</th><th>{t['in']}</th><th>{t['out']}</th></tr>")
    for f in overview.flows[:15]:
        parts.append(
            f"<tr><td>{escape(f.token_symbol)}</td><td>{_num(f.total_in)} ({f.count_in})</td>"
            f"<td>{_num(f.total_out)} ({f.count_out})</td></tr>"
        )
    parts.append("</table>")

    if risk is not None:
        parts.append(
            f"<h3>{t['risk']}: <span class='badge {risk.level}'>{risk.score}/100 · {escape(risk.level)}</span></h3>"
        )
        if risk.findings:
            parts.append("<table>")
            for f in risk.findings:
                evidence = "<br>".join(_mono(e) for e in f.evidence[:5])
                parts.append(
                    f"<tr><td><span class='badge {f.severity.value}'>{f.severity.value}</span></td>"
                    f"<td><b>{escape(f.title)}</b><br>{escape(f.detail)}<br>{evidence}</td></tr>"
                )
            parts.append("</table>")
        else:
            parts.append(f"<p class='muted'>{t['none']}</p>")

    if counterparties:
        parts.append(
            f"<h3>{t['counterparties']}</h3><table><tr><th>{t['address']}</th><th>{t['token']}</th>"
            f"<th>{t['received_from']}</th><th>{t['sent_to']}</th></tr>"
        )
        for c in counterparties[:15]:
            name = f"<br><b>{escape(labels[c.address])}</b>" if c.address in labels else ""
            parts.append(
                f"<tr><td>{_mono(c.address)}{name}</td><td>{escape(c.token_symbol)}</td>"
                f"<td>{_num(c.received_from)} ({c.count_in})</td><td>{_num(c.sent_to)} ({c.count_out})</td></tr>"
            )
        parts.append("</table>")
    return "".join(parts)


def trace_section(lang: str, title: str, data: dict) -> str:
    t = T[lang]
    start = data.get("start") or {}
    parts = [
        f"<div class='box'><b>{escape(title or t['trace'])}</b> — {escape(data.get('direction', ''))}, "
        f"{escape(data.get('method', ''))}<br>",
        f"{t['traced']}: <b>{_num(Decimal(str(data.get('traced_amount', 0))))} {escape(data.get('token_symbol', ''))}</b><br>",
        f"tx: {_mono(start.get('tx_hash'))}<br>{t['from']}: {_mono(start.get('from_address'))} → "
        f"{t['to']}: {_mono(start.get('to_address'))}</div>",
        f"<h3>{t['endpoints']}</h3><table><tr><th>{t['address']}</th><th>{t['reason']}</th>"
        f"<th>{t['amount']}</th><th>{t['confidence']}</th></tr>",
    ]
    for ep in data.get("endpoints", [])[:30]:
        label = ep.get("label") or {}
        name = f"<br><b>{escape(label.get('name', ''))}</b>" if label else ""
        parts.append(
            f"<tr><td>{_mono(ep['address'])}{name}</td><td>{escape(ep['reason'])}</td>"
            f"<td>{_num(Decimal(str(ep['amount'])))}</td><td>{float(ep['confidence']):.0%}</td></tr>"
        )
    parts.append(
        f"</table><h3>{t['path']}</h3><table><tr><th>#</th><th>{t['from']}</th><th>{t['to']}</th>"
        f"<th>{t['amount']}</th><th>{t['confidence']}</th><th>tx</th></tr>"
    )
    for f in data.get("flows", [])[:100]:
        parts.append(
            f"<tr><td>{f['hop']}</td><td>{_mono(f['from_address'])}</td><td>{_mono(f['to_address'])}</td>"
            f"<td>{_num(Decimal(str(f['traced_amount'])))} / {_num(Decimal(str(f['transfer_amount'])))}</td>"
            f"<td>{float(f['confidence']):.0%}</td><td>{_mono(f['tx_hash'])}<br>{_date(f['timestamp'])}</td></tr>"
        )
    parts.append("</table>")
    return "".join(parts)


def page(lang: str, title: str, body: str) -> str:
    t = T[lang]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"<!doctype html><html lang='{lang}' dir='{t['dir']}'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title>"
        "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700&display=swap'>"
        f"<style>{CSS}</style></head><body>"
        f"<h1>{escape(title)}</h1><div class='muted'>{t['generated']}: {now} · ChainTrace</div>"
        f"{body}<p class='muted' style='margin-top:40px'>{t['disclaimer']}</p></body></html>"
    )


def case_report(lang: str, case: Case, wallets: dict[tuple[Chain, str], str]) -> str:
    """`wallets` maps each address item to its already-rendered wallet section."""
    t = T[lang]
    body = []
    if case.description:
        body.append(f"<p>{escape(case.description)}</p>")
    notes = [i for i in case.items if i.kind == ItemKind.NOTE]
    if notes:
        body.append(f"<h2>{t['notes']}</h2>")
        for n in notes:
            body.append(f"<div class='box'><b>{escape(n.title)}</b><br>{escape(n.note)}</div>")
    for item in case.items:
        if item.kind == ItemKind.ADDRESS and item.chain and item.address:
            body.append(f"<h2>{t['address']}: {escape(item.title or item.address)}</h2>")
            if item.note:
                body.append(f"<p>{escape(item.note)}</p>")
            body.append(wallets.get((item.chain, item.address), ""))
    for item in case.items:
        if item.kind == ItemKind.TRACE and item.data:
            body.append(f"<h2>{t['trace']}</h2>")
            if item.note:
                body.append(f"<p>{escape(item.note)}</p>")
            body.append(trace_section(lang, item.title, item.data))
    return page(lang, f"{t['report']}: {case.title}", "".join(body))
