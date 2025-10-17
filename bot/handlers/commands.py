"""Command handlers."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from database import (
    get_active_rentals, sum_revenue_by_date_for_user, get_tool_by_name, 
    upsert_tool, list_tools, import_catalog_from_csv, reset_database,
    get_tool_by_id, update_tool_name, update_tool_price, delete_tool
)
from utils import parse_tool_and_price, moscow_today_str, format_daily_report_with_revenue
from .admin import check_admin_access, check_admin_callback
from .keyboards import (
    build_main_menu, build_rentals_list_kb, build_tools_list_kb, 
    build_tool_menu_kb, build_back_menu_kb, build_reset_confirm_kb
)
from .fsm import EditToolStates, ReportStates, RentalStates


def register_command_handlers(router: Router) -> None:
    """Регистрирует обработчики команд."""
    
    @router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        await state.clear()
        main_kb = build_main_menu()
        await message.answer(
            "👋 Привет! Отправь сообщение в формате: \n\n"
            "<b>Перфоратор Bosch 500</b>\n\n"
            "Где последняя цифра — цена в ₽ за сутки.",
            reply_markup=main_kb,
        )

    @router.message(Command("list"))
    async def cmd_list(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        await state.clear()
        rows = await get_active_rentals(user_id=message.from_user.id)
        if not rows:
            await message.answer("✅ Все инструменты возвращены. Активных аренд нет.")
            return
        await message.answer("📋 Активные аренды (оставшееся время):", reply_markup=build_rentals_list_kb(rows))

    @router.message(Command("report_now"))
    async def cmd_report_now(message: Message, scheduler) -> None:
        if not await check_admin_access(message):
            return
        
        await scheduler.send_daily_report_for_user(message.from_user.id)
        await message.answer("✅ Отчёт отправлен")

    @router.message(Command("report_today"))
    async def cmd_report_today(message: Message) -> None:
        if not await check_admin_access(message):
            return
        
        date = moscow_today_str()
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(Command("report"))
    async def cmd_report(message: Message) -> None:
        if not await check_admin_access(message):
            return
        
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
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(Command("expire_last"))
    async def cmd_expire_last(message: Message, scheduler) -> None:
        if not await check_admin_access(message):
            return
        
        rows = await get_active_rentals(user_id=message.from_user.id)
        if not rows:
            await message.answer("✅ Активных аренд нет")
            return
        rental_id = int(rows[0]["id"])  # последняя по ORDER BY id DESC в БД
        await scheduler.trigger_expiration_now(rental_id)
        await message.answer("⏱️ Тестовое уведомление отправлено")

    @router.message(Command("income_today"))
    async def cmd_income_today(message: Message) -> None:
        if not await check_admin_access(message):
            return
        
        date = moscow_today_str()
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(f"💰 Доход за {date}: {s}₽")

    @router.message(Command("income"))
    async def cmd_income(message: Message) -> None:
        if not await check_admin_access(message):
            return
        
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
    @router.message(Command("catalog"))
    async def cmd_catalog(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        items = await list_tools(limit=50)
        if not items:
            await message.answer("Каталог пуст. Импортируйте CSV или установите цены командой /setprice.")
            return
        await state.set_state(EditToolStates.choosing_tool)
        await message.answer("📚 Выберите инструмент для редактирования:", reply_markup=build_tools_list_kb(items))

    @router.message(Command("setprice"))
    async def cmd_setprice(message: Message) -> None:
        if not await check_admin_access(message):
            return
        
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

    @router.message(Command("import_catalog"))
    async def cmd_import_catalog(message: Message) -> None:
        if not await check_admin_access(message):
            return
        
        # Импортирует файл /app/data/catalog.csv
        from pathlib import Path
        path = Path("/app/data/catalog.csv")
        if not path.exists():
            await message.answer("Файл /app/data/catalog.csv не найден. Смонтируйте его в volume bot_data.")
            return
        count = await import_catalog_from_csv(str(path))
        await message.answer(f"✅ Импортировано позиций: {count}")

    # --- Reset database (testing) ---
    @router.message(Command("reset_db"))
    async def cmd_reset_db(message: Message) -> None:
        if not await check_admin_access(message):
            return
        
        await message.answer(
            "Вы уверены? Будут удалены все аренды, выручки и каталог.",
            reply_markup=build_reset_confirm_kb(),
        )

    # --- Text buttons handling ---
    @router.message(F.text == "📋 Список аренд")
    async def btn_list(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        await state.clear()
        rows = await get_active_rentals(user_id=message.from_user.id)
        if not rows:
            await message.answer("✅ Все инструменты возвращены. Активных аренд нет.")
            return
        await message.answer("📋 Активные аренды (оставшееся время):", reply_markup=build_rentals_list_kb(rows))

    @router.message(F.text == "📅 Отчёт по дате")
    async def btn_report_by_date(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        await state.set_state(ReportStates.waiting_date)
        await message.answer("Введите дату в формате YYYY-MM-DD", reply_markup=build_back_menu_kb())

    @router.message(F.text == "📊 Отчёт сейчас")
    async def btn_report_now(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        await state.clear()
        # То же, что /report_today
        date = moscow_today_str()
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(F.text == "📚 Каталог")
    async def btn_catalog(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        # Сбросим возможное состояние отчёта по дате, чтобы текст кнопки не обрабатывался как дата
        await state.clear()
        # Переиспользуем тот же сценарий FSM, что и команда /catalog
        items = await list_tools(limit=50)
        if not items:
            await message.answer("Каталог пуст. Импортируйте CSV или установите цены командой /setprice.")
            return
        await state.set_state(EditToolStates.choosing_tool)
        await message.answer("📚 Выберите инструмент для редактирования:", reply_markup=build_tools_list_kb(items))

    @router.message(F.text == "⬆️ Импорт CSV")
    async def btn_import_hint(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        await state.clear()
        await message.answer(
            "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\n"
            "Формат: <code>Название,Цена</code> (две колонки).",
            reply_markup=build_back_menu_kb(),
        )

    @router.message(F.text == "💵 Установить цену")
    async def btn_setprice_hint(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        await state.clear()
        await message.answer(
            "Используйте команду: /setprice &lt;название&gt; &lt;цена&gt;\n"
            "Пример: /setprice Перфоратор Bosch 500"
        )

    # --- CSV import by sending a file ---
    @router.message(F.document)
    async def on_document(message: Message) -> None:
        if not await check_admin_access(message):
            return
        
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

            uploads_dir = Path("/app/data/uploads")
            uploads_dir.mkdir(parents=True, exist_ok=True)
            dest_path = uploads_dir / (file_name or "catalog.csv")

            # Download file to destination
            await message.bot.download(doc, destination=str(dest_path))

            count = await import_catalog_from_csv(str(dest_path))
            await message.answer(f"✅ Импортировано позиций: {count}")
        except Exception as e:
            await message.answer("❌ Не удалось импортировать CSV. Проверьте формат: название,цена")

    # --- Main text handler for rental creation ---
    @router.message(F.text)
    async def add_rent_handler(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        # Проверяем, не находимся ли мы в FSM состоянии создания аренды
        current_state = await state.get_state()
        if current_state in [RentalStates.waiting_deposit, RentalStates.waiting_payment_method, 
                           RentalStates.waiting_delivery_type, RentalStates.waiting_address]:
            # Если в FSM состоянии, не обрабатываем здесь - пусть FSM обработчики работают
            return
        
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
        # Начинаем процесс создания аренды
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

        # Сохраняем данные инструмента в FSM
        await state.update_data(tool_name=tool_name, rent_price=rent_price)
        await state.set_state(RentalStates.waiting_deposit)

        # Спрашиваем залог
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Без залога", callback_data="deposit:0")],
            [InlineKeyboardButton(text="↩️ Отмена", callback_data="back_menu")]
        ])
        await message.answer(
            f"🔧 <b>{tool_name}</b> — {rent_price}₽/сутки\n\n"
            "💰 Какой залог оставили? (введите сумму или нажмите кнопку)",
            reply_markup=kb
        )