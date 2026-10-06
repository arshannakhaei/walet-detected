"""Gregorian -> Jalali (Persian calendar) dates, for reports read in Iran."""

from datetime import date, datetime

MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]  # fmt: skip
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_jalali(d: date) -> tuple[int, int, int]:
    """(year, month, day) in the Jalali calendar."""
    gy, gm, gd = d.year, d.month, d.day
    g_days = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    jy = 0 if gy <= 1600 else 979
    gy -= 621 if gy <= 1600 else 1600
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_days[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def fa_digits(text: str | int) -> str:
    return str(text).translate(_PERSIAN_DIGITS)


def jalali(d: date | datetime, month_name: bool = True) -> str:
    """"۲۴ شهریور ۱۴۰۱" (or "۱۴۰۱/۰۶/۲۴" without month names)."""
    y, m, day = to_jalali(d.date() if isinstance(d, datetime) else d)
    if month_name:
        return f"{fa_digits(day)} {MONTHS[m - 1]} {fa_digits(y)}"
    return fa_digits(f"{y:04d}/{m:02d}/{day:02d}")


def jalali_month(period: str) -> str:
    """"2022-09" -> the Jalali month its 15th day falls in ("شهریور ۱۴۰۱")."""
    year, month = (int(x) for x in period.split("-")[:2])
    y, m, _ = to_jalali(date(year, month, 15))
    return f"{MONTHS[m - 1]} {fa_digits(y)}"
