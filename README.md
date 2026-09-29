# ChainTrace — ردیابی تراکنش‌های کیف پول

ابزار تحلیل و ردیابی جریان پول در بلاکچین: آدرس یک ولت را می‌دهید، تراکنش‌هایش را از
APIهای عمومی (TronGrid و ...) می‌گیرد و نشان می‌دهد از چه ولت‌هایی پول آمده و به چه
ولت‌هایی رفته است.

> وضعیت: **فاز ۱** (هسته + شبکه ترون). داشبورد، گراف، ردیابی مبلغ، شبکه‌های دیگر، ربات تلگرام و MCP در فازهای بعدی.

## نصب و اجرا (ویندوز / مک / لینوکس)

پیش‌نیاز: Python 3.11 یا جدیدتر

```bash
python -m venv .venv
# ویندوز:  .venv\Scripts\activate      مک/لینوکس:  source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env        # مک/لینوکس: cp .env.example .env   (اختیاری)
python run.py
```

بعد در مرورگر: <http://127.0.0.1:8000/docs> — همه‌ی endpointها را می‌شود همان‌جا امتحان کرد.

کلید رایگان TronGrid (از trongrid.io) را اگر در `.env` بگذارید، سرعت و سقف درخواست‌ها بیشتر می‌شود.

## API (فاز ۱)

| Endpoint | توضیح |
|---|---|
| `GET /api/detect/{address}` | تشخیص شبکه از روی فرمت آدرس |
| `GET /api/wallet/{address}/overview` | موجودی، اولین/آخرین فعالیت، مجموع ورودی/خروجی هر توکن |
| `GET /api/wallet/{address}/counterparties` | از چه ولت‌هایی پول گرفته / به چه ولت‌هایی داده (به تفکیک توکن) |
| `GET /api/wallet/{address}/transfers` | لیست تراکنش‌ها با فیلتر |
| `POST /api/wallet/{address}/refresh` | دریافت مجدد از بلاکچین (نادیده گرفتن کش) |

فیلترها (برای `transfers` و `counterparties`): `token` (مثل `USDT` یا آدرس قرارداد)،
`direction` (`in`/`out`)، `min_amount`، `max_amount`، `start`، `end`، `counterparty`، `include_failed`.

داده‌ها در `backend/data/chaintrace.db` (SQLite) کش می‌شوند تا هر آدرس فقط یک‌بار در هر
`CACHE_TTL_SECONDS` دانلود شود.

## تست

```bash
pip install -r requirements-dev.txt
python -m pytest
```

تست‌ها بدون اینترنت و با پاسخ‌های شبیه‌سازی‌شده‌ی TronGrid اجرا می‌شوند.

## ساختار

```
backend/app/
  providers/   دریافت داده از هر بلاکچین (tron.py، ...) با rate limit و retry
  services/    تحلیل: addresses.py (تشخیص/تبدیل آدرس)، wallet.py (خلاصه، طرف‌حساب‌ها)
  api/         REST API
  db.py        کش SQLite
run.py         اجرای یک‌دستوری
```
