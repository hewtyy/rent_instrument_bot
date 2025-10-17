import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

import os
from database import get_rental_by_id, all_active_for_reschedule, get_active_rentals, sum_revenue_by_date_for_user
from utils import ts_to_moscow_date_str, moscow_today_str, format_daily_report_with_revenue

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, timezone: ZoneInfo) -> None:
        self.timezone = timezone
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.bot: Optional[Bot] = None

    async def start(self, bot: Bot) -> None:
        self.bot = bot
        self.scheduler.start()
        # Daily report at 21:00 local TZ (from env)
        self.scheduler.add_job(
            self._send_daily_report_job,
            CronTrigger(hour=21, minute=0, timezone=self.timezone),
            id="daily_report",
            replace_existing=True,
        )
        # Nightly flush removed - revenue is now recorded at rental creation
        # Reschedule expiration for existing active rentals
        await self._reschedule_all_active()
        logger.info("Scheduler started with timezone %s", self.timezone)

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    async def _reschedule_all_active(self) -> None:
        rows = await all_active_for_reschedule()
        for r in rows:
            await self.schedule_expiration_notification(r["id"], r["start_time"], r["user_id"], r["tool_name"])  # type: ignore[arg-type]

    async def schedule_expiration_notification(self, rental_id: int, start_time_ts: int, user_id: int, tool_name: str) -> None:
        if self.bot is None:
            raise RuntimeError("Scheduler bot not initialized")
        # Next execution is 24h after start_time
        dt = datetime.fromtimestamp(start_time_ts, tz=self.timezone) + timedelta(hours=24)
        # If time already passed, schedule immediate run (1 minute later to avoid flood)
        run_time = max(dt, datetime.now(tz=self.timezone) + timedelta(minutes=1))
        job_id = f"expire_{rental_id}"
        self.scheduler.add_job(
            self._expiration_job,
            DateTrigger(run_date=run_time, timezone=self.timezone),
            id=job_id,
            kwargs={"rental_id": rental_id, "user_id": user_id, "tool_name": tool_name},
            replace_existing=True,
        )
        logger.info("Scheduled expiration: rental_id=%s at %s", rental_id, run_time.isoformat())

    async def _expiration_job(self, rental_id: int, user_id: int, tool_name: str) -> None:
        if self.bot is None:
            return
        text = (
            f"⏰ Аренда инструмента \"{tool_name}\" закончилась.\n"
            f"Что хотите сделать?"
        )
        from bot_handlers import build_expiration_keyboard  # lazy import to avoid cycles
        kb = build_expiration_keyboard(rental_id)
        try:
            await self.bot.send_message(chat_id=user_id, text=text, reply_markup=kb)
        except Exception as e:
            logger.exception("Failed to send expiration notification: %s", e)

    async def _send_daily_report_job(self) -> None:
        if self.bot is None:
            return
        # In this simple implementation we send daily report to each user who has active rentals right now.
        # For a minimal MVP, we'll deduplicate by user ids present in active rentals.
        from database import get_active_rentals
        from utils import format_daily_report

        rows = await get_active_rentals()
        # Collect unique users
        user_ids = sorted({int(r["user_id"]) for r in rows})
        for uid in user_ids:
            user_rows = [r for r in rows if int(r["user_id"]) == uid]
            date = moscow_today_str()
            revenue_sum = await sum_revenue_by_date_for_user(date, uid)
            text = format_daily_report_with_revenue(date, user_rows, revenue_sum)
            try:
                await self.bot.send_message(chat_id=uid, text=text)
            except Exception as e:
                logger.exception("Failed to send daily report to %s: %s", uid, e)

        # Optional: send copy to admin if ADMIN_ID is set
        admin_id = os.getenv("ADMIN_ID")
        if admin_id:
            try:
                admin_uid = int(admin_id)
            except ValueError:
                admin_uid = None
            if admin_uid:
                date = moscow_today_str()
                # Сводим по всем пользователям (для админа)
                try:
                    # Простой суммарный доход админа за дату = сумма по всем users: используем уже посчитанную сумму частями
                    # Для простоты — посчитаем ещё раз по БД, но без user_id (сумма по всем)
                    from database import sum_revenue_by_date
                    total_rev = await sum_revenue_by_date(date)
                    text = f"📢 Админ-отчёт\n📅 {date}\n💵 Суммарная выручка: {total_rev}₽"
                    await self.bot.send_message(chat_id=admin_uid, text=text)
                except Exception as e:
                    logger.exception("Failed to send admin daily report: %s", e)

    # --- Helper methods for testing ---
    async def send_daily_report_for_user(self, user_id: int) -> None:
        if self.bot is None:
            return
        from database import get_active_rentals
        from utils import format_daily_report

        rows = await get_active_rentals(user_id=user_id)
        text = format_daily_report(rows)
        try:
            await self.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logger.exception("Failed to send on-demand daily report to %s: %s", user_id, e)

    async def trigger_expiration_now(self, rental_id: int) -> None:
        if self.bot is None:
            return
        row = await get_rental_by_id(rental_id)
        if not row:
            return
        await self._expiration_job(rental_id=rental_id, user_id=int(row["user_id"]), tool_name=row["tool_name"])   


