from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo
import os

logger = logging.getLogger(__name__)


def parse_tool_and_price(text: str) -> Optional[Tuple[str, int]]:
    parts = text.strip().rsplit(" ", 1)
    if len(parts) != 2:
        return None
    name, price_str = parts[0].strip(), parts[1].strip()
    if not name:
        return None
    try:
        price = int(price_str)
        if price <= 0:
            return None
    except ValueError:
        return None
    return name, price


def format_active_list(rows: List[dict]) -> str:
    if not rows:
        return "✅ Все инструменты возвращены. Активных аренд нет."
    lines = ["📋 Активные аренды:"]
    total = 0
    for r in rows:
        lines.append(f"- {r['tool_name']} — {r['rent_price']}₽")
        total += int(r["rent_price"]) or 0
    lines.append(f"💰 Итого: {total}₽")
    return "\n".join(lines)


def format_daily_report(rows: List[dict]) -> str:
    if not rows:
        return "📊 Ежедневный отчёт:\n✅ Активных аренд нет."
    lines = ["📊 Ежедневный отчёт:"]
    total = 0
    for r in rows:
        lines.append(f"- {r['tool_name']} — {r['rent_price']}₽")
        total += int(r["rent_price"]) or 0
    lines.append(f"💰 Итого: {total}₽")
    return "\n".join(lines)


def utc_now_ts() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("TZ", "Asia/Tokyo"))


def moscow_today_str() -> str:
    tz = _tz()
    return datetime.now(tz=tz).strftime("%Y-%m-%d")


def ts_to_moscow_date_str(ts: int) -> str:
    tz = _tz()
    return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d")


def moscow_yesterday_str() -> str:
    tz = _tz()
    now = datetime.now(tz=tz)
    y = now.replace(hour=0, minute=0, second=0, microsecond=0)  # начало суток
    from datetime import timedelta
    y = y - timedelta(days=1)
    return y.strftime("%Y-%m-%d")


def format_daily_report_with_revenue(date: str, rows: List[dict], revenue_sum: int) -> str:
    # Сводный отчёт: активные аренды + фактическая выручка за дату
    if not rows:
        base = "📊 Ежедневный отчёт:\n✅ Активных аренд нет."
    else:
        lines = ["📊 Ежедневный отчёт:"]
        total_deposits = 0
        for r in rows:
            deposit = int(r.get("deposit", 0))
            payment_method = r.get("payment_method", "cash")
            delivery_type = r.get("delivery_type", "pickup")
            
            payment_icon = "💵" if payment_method == "cash" else "💳"
            delivery_icon = "🚚" if delivery_type == "delivery" else "🏠"
            
            lines.append(f"- {r['tool_name']} — {r['rent_price']}₽ {payment_icon}{delivery_icon}")
            if deposit > 0:
                lines.append(f"  💰 Залог: {deposit}₽")
                total_deposits += deposit
        
        if total_deposits > 0:
            lines.append(f"\n💰 Общая сумма залогов: {total_deposits}₽")
        
        base = "\n".join(lines)
    return base + f"\n📅 Дата: {date}\n💵 Выручка за день: {revenue_sum}₽"


def format_remaining_time(start_time_ts: int) -> str:
    # Расчёт в POSIX-секундах, чтобы исключить любые эффекты TZ/DST
    import time
    day_sec = 24 * 3600
    now_sec = int(time.time())
    total_sec = (int(start_time_ts) + day_sec) - now_sec
    if total_sec <= 0:
        return "срок истёк"
    # ceil до минуты
    minute = 60
    total_sec = ((total_sec + minute - 1) // minute) * minute
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def format_local_end_time_hhmm(start_time_ts: int) -> str:
    from datetime import timedelta
    tz = _tz()
    end_dt = datetime.fromtimestamp(int(start_time_ts), tz=tz) + timedelta(hours=24)
    return end_dt.strftime("%H:%M")


