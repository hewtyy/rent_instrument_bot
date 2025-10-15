import asyncio
import logging
import os
from contextlib import suppress
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from database import init_db, import_catalog_from_csv
from scheduler import SchedulerService
from bot_handlers import register_handlers


async def main() -> None:
    load_dotenv()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("tool_rent_bot")

    # Ensure TZ is set for the process and APScheduler
    tz_name = os.getenv("TZ", "Asia/Tokyo")
    os.environ["TZ"] = tz_name

    # Initialize DB (creates tables if not exist)
    await init_db()
    # Import catalog on startup if file exists
    from pathlib import Path
    catalog_path = Path("/app/data/catalog.csv")
    if catalog_path.exists():
        try:
            count = await import_catalog_from_csv(str(catalog_path))
            logger.info("Imported catalog on startup: %s items", count)
        except Exception:
            logger.exception("Failed to import catalog on startup")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in environment")

    bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # Scheduler setup
    scheduler = SchedulerService(timezone=ZoneInfo(tz_name))
    await scheduler.start(bot)

    # Register handlers
    register_handlers(dp, scheduler)

    logger.info("Starting polling...")
    try:
        # Явно укажем типы апдейтов на основе зарегистрированных хэндлеров
        allowed = dp.resolve_used_update_types()
        logger.info("Allowed updates: %s", allowed)
        await dp.start_polling(bot, allowed_updates=allowed)
    finally:
        with suppress(Exception):
            await scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())


