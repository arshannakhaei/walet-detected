# ChainTrace — ردیابی تراکنش‌های کیف پول

ابزار تحلیل و ردیابی جریان پول در بلاکچین: آدرس یک ولت را می‌دهید، تراکنش‌هایش را از
APIهای عمومی (TronGrid و ...) می‌گیرد و نشان می‌دهد از چه ولت‌هایی پول آمده و به چه
ولت‌هایی رفته است.

> وضعیت: **فاز ۲** (هسته + ترون + گراف چندلایه + ردیابی مبلغ + برچسب‌ها). داشبورد، شبکه‌های دیگر، ربات تلگرام و MCP در فازهای بعدی.

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

## API

| Endpoint | توضیح |
|---|---|
| `GET /api/detect/{address}` | تشخیص شبکه از روی فرمت آدرس |
| `GET /api/wallet/{address}/overview` | موجودی، اولین/آخرین فعالیت، مجموع ورودی/خروجی هر توکن |
| `GET /api/wallet/{address}/counterparties` | از چه ولت‌هایی پول گرفته / به چه ولت‌هایی داده (به تفکیک توکن) |
| `GET /api/wallet/{address}/transfers` | لیست تراکنش‌ها با فیلتر |
| `POST /api/wallet/{address}/refresh` | دریافت مجدد از بلاکچین (نادیده گرفتن کش) |
| `GET /api/graph/{address}` | گراف چندلایه: `depth_in` لایه فرستنده‌ها و `depth_out` لایه گیرنده‌ها |
| `POST /api/trace` | ردیابی یک مبلغ مشخص، hop به hop، رو به جلو یا عقب |
| `GET/PUT/DELETE /api/labels/...` | برچسب آدرس‌ها (صرافی، میکسر، شخصی، ...) |

فیلترها (برای `transfers` و `counterparties`): `token` (مثل `USDT` یا آدرس قرارداد)،
`direction` (`in`/`out`)، `min_amount`، `max_amount`، `start`، `end`، `counterparty`، `include_failed`.

### گراف (`/api/graph`)

از ولت شروع می‌کند و لایه به لایه جلو می‌رود: «به کی پول داده ← آن‌ها به کی پول دادند ← ...» و
همین‌طور رو به عقب برای منبع پول. پارامترها: `max_nodes`، `max_children` (چند طرف‌حساب بزرگ هر
ولت)، و `follow_time` (فقط انتقال‌هایی که **بعد** از رسیدن پول به آن ولت انجام شده‌اند) + همه‌ی فیلترهای بالا.
ولت‌های خیلی شلوغ (بیش از `HUB_THRESHOLD` تراکنش، معمولاً صرافی/سرویس) و آدرس‌های برچسب‌خورده
به‌عنوان صرافی/بریج/میکسر باز نمی‌شوند.

### ردیابی مبلغ (`/api/trace`)

```json
{"address": "T...", "tx_hash": "...", "direction": "forward", "method": "fifo", "max_hops": 6, "min_amount": 1}
```

- `direction`: `forward` = این پول کجا رفت؟ / `backward` = این پول از کجا آمد؟
- داخل یک ولت پول‌ها قاطی می‌شوند، پس دو قانون به کار می‌رود:
  1. **تطبیق دقیق**: اگر کمی بعد از دریافت، تقریباً همان مبلغ (±`tolerance`) خارج شده، همان در نظر گرفته می‌شود (اطمینان ۹۵٪).
  2. **حسابداری لات**: بقیه با `fifo` (اول قدیمی‌ترین پول خرج می‌شود) یا `lifo` (اول جدیدترین) تقسیم می‌شود (اطمینان ۷۰٪).
- اطمینان در طول مسیر ضرب می‌شود. خروجی: `flows` (هر انتقال با مقدار ردیابی‌شده و اطمینان)،
  `endpoints` (پول کجا متوقف شد: `labeled` صرافی، `unspent` مانده در ولت، `hub`، `max_hops`، ...) و `summary`.

### برچسب‌ها

لیست پایه در `backend/data/labels/<chain>.json` است (می‌توانید آدرس صرافی‌ها را اضافه کنید)، و
برچسب‌های خودتان با `PUT /api/labels/tron/{address}` در دیتابیس ذخیره می‌شوند.

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
  services/    addresses.py (تشخیص/تبدیل آدرس)، wallet.py (خلاصه، طرف‌حساب‌ها)،
               graph.py (گراف چندلایه)، tracer.py (ردیابی مبلغ)، labels.py (برچسب‌ها)
  api/         REST API
  db.py        کش SQLite
run.py         اجرای یک‌دستوری
```
