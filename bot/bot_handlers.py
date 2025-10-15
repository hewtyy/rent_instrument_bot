import logging
from typing import Tuple

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from database import add_rental, get_active_rentals, renew_rental, close_rental, get_rental_by_id, add_revenue, sum_revenue_by_date_for_user, get_tool_by_name, upsert_tool, list_tools, import_catalog_from_csv, reset_database, get_tool_by_id, update_tool_name, update_tool_price, delete_tool, sum_revenue_by_date
from utils import parse_tool_and_price, format_active_list, ts_to_moscow_date_str, moscow_today_str, moscow_yesterday_str

logger = logging.getLogger(__name__)


def build_expiration_keyboard(rental_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Продлить аренду", callback_data=f"renew:{rental_id}"),
            InlineKeyboardButton(text="❌ Забрал инструмент", callback_data=f"close:{rental_id}"),
        ]
    ])
    return kb


class EditToolStates(StatesGroup):
    choosing_tool = State()
    tool_menu = State()
    renaming = State()
    pricing = State()


class ReportStates(StatesGroup):
    waiting_date = State()


def register_handlers(dp, scheduler) -> None:
    router = Router()

    def build_main_menu() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📋 Список аренд"), KeyboardButton(text="📊 Отчёт сейчас")],
                [KeyboardButton(text="📅 Отчёт по дате"), KeyboardButton(text="📚 Каталог")],
                [KeyboardButton(text="⬆️ Импорт CSV"), KeyboardButton(text="💵 Установить цену")],
            ],
            resize_keyboard=True,
        )

    @router.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        main_kb = build_main_menu()
        await message.answer(
            "👋 Привет! Отправь сообщение в формате: \n\n"
            "<b>Перфоратор Bosch 500</b>\n\n"
            "Где последняя цифра — цена в ₽ за сутки.",
            reply_markup=main_kb,
        )

    @router.message(Command("list"))
    async def cmd_list(message: Message) -> None:
        rows = await get_active_rentals(user_id=message.from_user.id)
        await message.answer(format_active_list(rows))

    @router.message(Command("report_now"))
    async def cmd_report_now(message: Message) -> None:
        await scheduler.send_daily_report_for_user(message.from_user.id)
        await message.answer("✅ Отчёт отправлен")

    @router.message(Command("report_today"))
    async def cmd_report_today(message: Message) -> None:
        date = moscow_today_str()
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        from utils import format_daily_report_with_revenue
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(Command("report"))
    async def cmd_report(message: Message) -> None:
        # /report YYYY-MM-DD
        text = (message.text or "").strip()
        parts = text.split()
        if len(parts) != 2:
            await message.answer("Формат: /report YYYY-MM-DD")
            return
        date = parts[1]
        if len(date) != 10 or date[4] != '-' or date[7] != '-':
            await message.answer("Формат: /report YYYY-MM-DD")
            return
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        from utils import format_daily_report_with_revenue
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(Command("expire_last"))
    async def cmd_expire_last(message: Message) -> None:
        rows = await get_active_rentals(user_id=message.from_user.id)
        if not rows:
            await message.answer("✅ Активных аренд нет")
            return
        rental_id = int(rows[0]["id"])  # последняя по ORDER BY id DESC в БД — мы берём первую из списка /list
        await scheduler.trigger_expiration_now(rental_id)
        await message.answer("⏱️ Тестовое уведомление отправлено")

    @router.message(Command("income_today"))
    async def cmd_income_today(message: Message) -> None:
        date = moscow_today_str()
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(f"💰 Доход за {date}: {s}₽")

    @router.message(Command("income"))
    async def cmd_income(message: Message) -> None:
        # Ожидаем формат: /income YYYY-MM-DD
        text = (message.text or "").strip()
        parts = text.split()
        if len(parts) != 2:
            await message.answer("Формат: /income YYYY-MM-DD")
            return
        date = parts[1]
        # Простейшая валидация формата
        if len(date) != 10 or date[4] != '-' or date[7] != '-':
            await message.answer("Формат: /income YYYY-MM-DD")
            return
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(f"💰 Доход за {date}: {s}₽")

    # --- Catalog commands ---
    def build_tools_list_kb(items: list[dict]) -> InlineKeyboardMarkup:
        rows = []
        for it in items:
            rows.append([InlineKeyboardButton(text=f"{it['name']} ({it['price']}₽)", callback_data=f"tool_open:{it['id']}")])
        rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_menu")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def build_tool_menu_kb(tool_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"tool_do_rename:{tool_id}")],
            [InlineKeyboardButton(text="💵 Изменить цену", callback_data=f"tool_do_price:{tool_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"tool_do_delete:{tool_id}")],
            [InlineKeyboardButton(text="↩️ К списку", callback_data="tools_list")],
        ])

    @router.message(Command("catalog"))
    async def cmd_catalog(message: Message, state: FSMContext) -> None:
        items = await list_tools(limit=50)
        if not items:
            await message.answer("Каталог пуст. Импортируйте CSV или установите цены командой /setprice.")
            return
        await state.set_state(EditToolStates.choosing_tool)
        await message.answer("📚 Выберите инструмент для редактирования:", reply_markup=build_tools_list_kb(items))

    @router.message(Command("setprice"))
    async def cmd_setprice(message: Message) -> None:
        # /setprice <название> <цена>
        text = (message.text or "").strip()
        parts = text.split(" ", 2)
        if len(parts) < 3:
            await message.answer("Формат: /setprice <название> <цена>")
            return
        _, name, price_str = parts
        try:
            price = int(price_str)
            if price <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Цена должна быть положительным числом")
            return
        await upsert_tool(name, price)
        await message.answer(f"✅ Установлена цена для \"{name}\": {price}₽")

    @router.callback_query(F.data == "tools_list")
    async def cb_tools_list(callback: CallbackQuery, state: FSMContext) -> None:
        items = await list_tools(limit=50)
        await state.set_state(EditToolStates.choosing_tool)
        await callback.message.edit_text("📚 Выберите инструмент для редактирования:")
        await callback.message.answer(reply_markup=build_tools_list_kb(items), text=" ")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_open:"))
    async def cb_tool_open(callback: CallbackQuery, state: FSMContext) -> None:
        tool_id = int(callback.data.split(":", 1)[1])
        tool = await get_tool_by_id(tool_id)
        if not tool:
            await callback.answer("Не найдено", show_alert=True)
            return
        await state.update_data(tool_id=tool_id)
        await state.set_state(EditToolStates.tool_menu)
        await callback.message.edit_text(f"🔧 {tool['name']} — {tool['price']}₽", reply_markup=build_tool_menu_kb(tool_id))
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_do_rename:"))
    async def cb_tool_do_rename(callback: CallbackQuery, state: FSMContext) -> None:
        tool_id = int(callback.data.split(":", 1)[1])
        await state.update_data(tool_id=tool_id)
        await state.set_state(EditToolStates.renaming)
        await callback.message.edit_text("Введите новое название:")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_do_price:"))
    async def cb_tool_do_price(callback: CallbackQuery, state: FSMContext) -> None:
        tool_id = int(callback.data.split(":", 1)[1])
        await state.update_data(tool_id=tool_id)
        await state.set_state(EditToolStates.pricing)
        await callback.message.edit_text("Введите новую цену (число):")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_do_delete:"))
    async def cb_tool_do_delete(callback: CallbackQuery, state: FSMContext) -> None:
        tool_id = int(callback.data.split(":", 1)[1])
        await delete_tool(tool_id)
        await callback.message.edit_text("✅ Инструмент удалён")
        await callback.answer()

    @router.message(EditToolStates.renaming, F.text)
    async def state_renaming(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        tool_id = int(data.get("tool_id"))
        new_name = (message.text or "").strip()
        if not new_name:
            await message.answer("Название не может быть пустым")
            return
        await update_tool_name(tool_id, new_name)
        tool = await get_tool_by_id(tool_id)
        await state.set_state(EditToolStates.tool_menu)
        await message.answer(f"✅ Название обновлено\n🔧 {tool['name']} — {tool['price']}₽", reply_markup=build_tool_menu_kb(tool_id))

    @router.message(EditToolStates.pricing, F.text)
    async def state_pricing(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        tool_id = int(data.get("tool_id"))
        try:
            new_price = int((message.text or "").strip())
            if new_price <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Цена должна быть положительным числом")
            return
        await update_tool_price(tool_id, new_price)
        tool = await get_tool_by_id(tool_id)
        await state.set_state(EditToolStates.tool_menu)
        await message.answer(f"✅ Цена обновлена\n🔧 {tool['name']} — {tool['price']}₽", reply_markup=build_tool_menu_kb(tool_id))

    @router.message(Command("import_catalog"))
    async def cmd_import_catalog(message: Message) -> None:
        # Импортирует файл /app/data/catalog.csv
        from pathlib import Path
        path = Path("/app/data/catalog.csv")
        if not path.exists():
            await message.answer("Файл /app/data/catalog.csv не найден. Смонтируйте его в volume bot_data.")
            return
        count = await import_catalog_from_csv(str(path))
        await message.answer(f"✅ Импортировано позиций: {count}")

    # --- Text buttons handling ---
    @router.message(F.text == "📋 Список аренд")
    async def btn_list(message: Message) -> None:
        rows = await get_active_rentals(user_id=message.from_user.id)
        await message.answer(format_active_list(rows))

    @router.message(F.text == "📅 Отчёт по дате")
    async def btn_report_by_date(message: Message, state: FSMContext) -> None:
        await state.set_state(ReportStates.waiting_date)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]
        ])
        await message.answer("Введите дату в формате YYYY-MM-DD", reply_markup=back_kb)

    @router.message(F.text == "📊 Отчёт сейчас")
    async def btn_report_now(message: Message) -> None:
        # То же, что /report_today
        from utils import format_daily_report_with_revenue
        date = moscow_today_str()
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(ReportStates.waiting_date, F.text)
    async def state_report_by_date(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if len(text) != 10 or text[4] != '-' or text[7] != '-':
            await message.answer("Формат: YYYY-MM-DD")
            return
        from utils import format_daily_report_with_revenue
        date = text
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]
        ])
        await message.answer(format_daily_report_with_revenue(date, rows, s), reply_markup=back_kb)
        await state.clear()

    @router.message(F.text == "📚 Каталог")
    async def btn_catalog(message: Message, state: FSMContext) -> None:
        # Сбросим возможное состояние отчёта по дате, чтобы текст кнопки не обрабатывался как дата
        await state.clear()
        # Переиспользуем тот же сценарий FSM, что и команда /catalog
        await cmd_catalog(message, state)

    @router.message(F.text == "⬆️ Импорт CSV")
    async def btn_import_hint(message: Message) -> None:
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]
        ])
        await message.answer(
            "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\n"
            "Формат: <code>Название,Цена</code> (две колонки).",
            reply_markup=back_kb,
        )

    @router.callback_query(F.data == "back_menu")
    async def cb_back_menu(callback: CallbackQuery) -> None:
        await callback.message.answer("Главное меню", reply_markup=build_main_menu())
        await callback.answer()

    # --- Reset database (testing) ---
    @router.message(Command("reset_db"))
    async def cmd_reset_db(message: Message) -> None:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⚠️ Да, очистить всё", callback_data="reset_db_confirm"),
                InlineKeyboardButton(text="Отмена", callback_data="back_menu"),
            ]
        ])
        await message.answer(
            "Вы уверены? Будут удалены все аренды, выручки и каталог.",
            reply_markup=kb,
        )

    @router.callback_query(F.data == "reset_db_confirm")
    async def cb_reset_db(confirm: CallbackQuery) -> None:
        await reset_database()
        await confirm.message.edit_text("✅ База очищена. Можно начинать заново.")
        await confirm.answer()

    @router.message(F.text == "💵 Установить цену")
    async def btn_setprice_hint(message: Message) -> None:
        await message.answer(
            "Используйте команду: /setprice &lt;название&gt; &lt;цена&gt;\n"
            "Пример: /setprice Перфоратор Bosch 500"
        )

    # --- CSV import by sending a file ---
    @router.message(F.document)
    async def on_document(message: Message) -> None:
        doc = message.document
        if not doc:
            return
        file_name = (doc.file_name or "").lower()
        mime = (doc.mime_type or "").lower()
        if not (file_name.endswith('.csv') or 'csv' in mime):
            # Ignore non-CSV documents
            return
        await message.answer("⬇️ Получил CSV, начинаю импорт...")
        try:
            from pathlib import Path
            import asyncio

            uploads_dir = Path("/app/data/uploads")
            uploads_dir.mkdir(parents=True, exist_ok=True)
            dest_path = uploads_dir / (file_name or "catalog.csv")

            # Download file to destination
            await message.bot.download(doc, destination=str(dest_path))

            count = await import_catalog_from_csv(str(dest_path))
            await message.answer(f"✅ Импортировано позиций: {count}")
        except Exception as e:
            await message.answer("❌ Не удалось импортировать CSV. Проверьте формат: название,цена")

    @router.message(F.text)
    async def add_rent_handler(message: Message) -> None:
        text = (message.text or "").strip()
        # Команды редактирования каталога в простом текстовом формате
        if text.startswith("rename "):
            parts = text.split(" ", 2)
            if len(parts) < 3:
                await message.answer("Формат: rename <id> <НовоеНазвание>")
                return
            try:
                tool_id = int(parts[1])
            except ValueError:
                await message.answer("ID должен быть числом")
                return
            new_name = parts[2].strip()
            if not new_name:
                await message.answer("Название не может быть пустым")
                return
            await update_tool_name(tool_id, new_name)
            await message.answer("✅ Название обновлено")
            return

        if text.startswith("price "):
            parts = text.split()
            if len(parts) != 3:
                await message.answer("Формат: price <id> <цена>")
                return
            try:
                tool_id = int(parts[1])
                new_price = int(parts[2])
                if new_price <= 0:
                    raise ValueError
            except ValueError:
                await message.answer("ID и цена должны быть положительными числами")
                return
            await update_tool_price(tool_id, new_price)
            await message.answer("✅ Цена обновлена")
            return
        parsed = parse_tool_and_price(text)
        if parsed is None:
            # Если указан только инструмент, попробуем взять цену из каталога
            catalog_row = await get_tool_by_name(text)
            if not catalog_row:
                await message.answer(
                    "❗️ Формат: <b>Название инструмента Цена</b>\nНапример: <b>Перфоратор Bosch 500</b>\n"
                    "Или добавьте инструмент в каталог через /setprice или импортируйте каталог."
                )
                return
            tool_name, rent_price = catalog_row["name"], int(catalog_row["price"]) 
        else:
            tool_name, rent_price = parsed
        rental_id = await add_rental(tool_name, rent_price, message.from_user.id)
        # Schedule expiration in 24h
        await scheduler.schedule_expiration_notification(
            rental_id=rental_id,
            start_time_ts=(await get_rental_by_id(rental_id))["start_time"],
            user_id=message.from_user.id,
            tool_name=tool_name,
        )
        await message.answer(f"✅ Инструмент \"{tool_name}\" добавлен в аренду за {rent_price}₽/сутки")

    @router.callback_query(F.data.startswith("renew:"))
    async def on_renew(callback: CallbackQuery) -> None:
        rental_id = int(callback.data.split(":", 1)[1])
        row = await get_rental_by_id(rental_id)
        if row:
            # Начисляем выручку на текущую локальную дату (TZ из окружения)
            date_key = moscow_today_str()
            await add_revenue(date_key, rental_id, int(row["rent_price"]))
            await renew_rental(rental_id)
            await scheduler.schedule_expiration_notification(
                rental_id=rental_id,
                start_time_ts=row["start_time"],
                user_id=row["user_id"],
                tool_name=row["tool_name"],
            )
        await callback.message.edit_text("✅ Аренда продлена на 24 часа")
        await callback.answer()

    @router.callback_query(F.data.startswith("close:"))
    async def on_close(callback: CallbackQuery) -> None:
        rental_id = int(callback.data.split(":", 1)[1])
        row = await get_rental_by_id(rental_id)
        if row:
            # Начисляем выручку на текущую локальную дату (TZ из окружения)
            date_key = moscow_today_str()
            await add_revenue(date_key, rental_id, int(row["rent_price"]))
        await close_rental(rental_id)
        await callback.message.edit_text("🔒 Аренда инструмента завершена")
        await callback.answer()

    dp.include_router(router)


