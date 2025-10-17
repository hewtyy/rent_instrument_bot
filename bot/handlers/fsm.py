"""FSM states and handlers."""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database import (
    add_rental, get_rental_by_id, get_tool_by_name, upsert_tool, 
    list_tools, get_tool_by_id, update_tool_name, update_tool_price, delete_tool,
    get_active_rentals, sum_revenue_by_date_for_user, add_revenue
)
from utils import parse_tool_and_price, moscow_today_str, format_daily_report_with_revenue
from .admin import check_admin_access, check_admin_callback
from .keyboards import (
    build_main_menu, build_rentals_list_kb, build_tool_menu_kb, 
    build_tools_list_kb, build_back_menu_kb
)


class RentalStates(StatesGroup):
    """Состояния для создания аренды."""
    waiting_deposit = State()
    waiting_payment_method = State()
    waiting_delivery_type = State()
    waiting_address = State()


class EditToolStates(StatesGroup):
    """Состояния для редактирования каталога."""
    choosing_tool = State()
    tool_menu = State()
    renaming = State()
    pricing = State()


class ReportStates(StatesGroup):
    """Состояния для отчётов."""
    waiting_date = State()


async def create_rental_from_fsm(message_or_callback, state: FSMContext, scheduler) -> None:
    """Создает аренду из данных FSM."""
    data = await state.get_data()
    tool_name = data.get("tool_name")
    rent_price = data.get("rent_price")
    deposit = data.get("deposit", 0)
    payment_method = data.get("payment_method", "cash")
    delivery_type = data.get("delivery_type", "pickup")
    address = data.get("address", "")

    if not tool_name or not rent_price:
        await message_or_callback.answer("❌ Ошибка: данные аренды не найдены. Попробуйте создать аренду заново.")
        await state.clear()
        return

    # Получаем user_id в зависимости от типа объекта
    if hasattr(message_or_callback, 'from_user'):
        user_id = message_or_callback.from_user.id
    else:
        user_id = message_or_callback.message.from_user.id

    # Создаем аренду
    rental_id = await add_rental(
        tool_name=tool_name,
        rent_price=rent_price,
        user_id=user_id,
        deposit=deposit,
        payment_method=payment_method,
        delivery_type=delivery_type,
        address=address
    )

    # Записываем выручку
    date_key = moscow_today_str()
    await add_revenue(date_key, rental_id, rent_price)

    # Планируем уведомление
    rental_data = await get_rental_by_id(rental_id)
    await scheduler.schedule_expiration_notification(
        rental_id=rental_id,
        start_time_ts=rental_data["start_time"],
        user_id=user_id,
        tool_name=tool_name,
    )

    # Формируем сообщение
    payment_text = "💵 Наличные" if payment_method == "cash" else "💳 Перевод"
    delivery_text = "🚚 Доставка" if delivery_type == "delivery" else "🏠 Самовывоз"

    result_text = (
        f"✅ <b>Аренда создана!</b>\n\n"
        f"🔧 <b>{tool_name}</b> — {rent_price}₽/сутки\n"
        f"💰 Залог: {deposit}₽\n"
        f"{payment_text}\n"
        f"{delivery_text}"
    )

    if delivery_type == "delivery" and address:
        result_text += f"\n📍 Адрес: {address}"

    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(result_text)
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(result_text)

    await state.clear()


def register_fsm_handlers(router: Router, scheduler) -> None:
    """Регистрирует FSM обработчики."""
    
    # --- FSM handlers for rental creation ---
    @router.message(RentalStates.waiting_deposit, F.text)
    async def state_waiting_deposit(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        text = (message.text or "").strip()
        
        # Проверяем, не нажал ли пользователь кнопку главного меню
        main_buttons = {"📋 Список аренд", "📊 Отчёт сейчас", "📅 Отчёт по дате", "📚 Каталог", "⬆️ Импорт CSV", "💵 Установить цену"}
        if text in main_buttons:
            await state.clear()
            # Роутинг на соответствующий сценарий
            if text == "📋 Список аренд":
                rows = await get_active_rentals(user_id=message.from_user.id)
                if not rows:
                    await message.answer("✅ Все инструменты возвращены. Активных аренд нет.")
                    return
                await message.answer("📋 Активные аренды (оставшееся время):", reply_markup=build_rentals_list_kb(rows))
                return
            if text == "📊 Отчёт сейчас":
                date = moscow_today_str()
                rows = await get_active_rentals(user_id=message.from_user.id)
                s = await sum_revenue_by_date_for_user(date, message.from_user.id)
                await message.answer(format_daily_report_with_revenue(date, rows, s))
                return
            if text == "📚 Каталог":
                items = await list_tools(limit=50)
                if not items:
                    await message.answer("Каталог пуст. Импортируйте CSV или установите цены командой /setprice.")
                    return
                await state.set_state(EditToolStates.choosing_tool)
                await message.answer("📚 Выберите инструмент для редактирования:", reply_markup=build_tools_list_kb(items))
                return
            if text == "⬆️ Импорт CSV":
                await message.answer(
                    "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\n"
                    "Формат: <code>Название,Цена</code> (две колонки).",
                    reply_markup=build_back_menu_kb(),
                )
                return
            if text == "💵 Установить цену":
                await message.answer(
                    "Используйте команду: /setprice &lt;название&gt; &lt;цена&gt;\nПример: /setprice Перфоратор Bosch 500"
                )
                return
            return
        
        try:
            deposit = int(text)
            if deposit < 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите корректную сумму залога (число ≥ 0)")
            return
        
        await state.update_data(deposit=deposit)
        await state.set_state(RentalStates.waiting_payment_method)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💵 Наличные", callback_data="payment:cash")],
            [InlineKeyboardButton(text="💳 Перевод", callback_data="payment:transfer")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_deposit")]
        ])
        await message.answer("💳 Способ оплаты:", reply_markup=kb)

    @router.message(RentalStates.waiting_address, F.text)
    async def state_waiting_address(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        text = (message.text or "").strip()
        
        # Проверяем, не нажал ли пользователь кнопку главного меню
        main_buttons = {"📋 Список аренд", "📊 Отчёт сейчас", "📅 Отчёт по дате", "📚 Каталог", "⬆️ Импорт CSV", "💵 Установить цену"}
        if text in main_buttons:
            await state.clear()
            # Роутинг на соответствующий сценарий
            if text == "📋 Список аренд":
                rows = await get_active_rentals(user_id=message.from_user.id)
                if not rows:
                    await message.answer("✅ Все инструменты возвращены. Активных аренд нет.")
                    return
                await message.answer("📋 Активные аренды (оставшееся время):", reply_markup=build_rentals_list_kb(rows))
                return
            if text == "📊 Отчёт сейчас":
                date = moscow_today_str()
                rows = await get_active_rentals(user_id=message.from_user.id)
                s = await sum_revenue_by_date_for_user(date, message.from_user.id)
                await message.answer(format_daily_report_with_revenue(date, rows, s))
                return
            if text == "📚 Каталог":
                items = await list_tools(limit=50)
                if not items:
                    await message.answer("Каталог пуст. Импортируйте CSV или установите цены командой /setprice.")
                    return
                await state.set_state(EditToolStates.choosing_tool)
                await message.answer("📚 Выберите инструмент для редактирования:", reply_markup=build_tools_list_kb(items))
                return
            if text == "⬆️ Импорт CSV":
                await message.answer(
                    "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\n"
                    "Формат: <code>Название,Цена</code> (две колонки).",
                    reply_markup=build_back_menu_kb(),
                )
                return
            if text == "💵 Установить цену":
                await message.answer(
                    "Используйте команду: /setprice &lt;название&gt; &lt;цена&gt;\nПример: /setprice Перфоратор Bosch 500"
                )
                return
            return
        
        if not text:
            await message.answer("Введите адрес доставки")
            return
        
        await state.update_data(address=text)
        await create_rental_from_fsm(message, state, scheduler)

    # --- FSM callback handlers ---
    @router.callback_query(F.data.startswith("deposit:"))
    async def cb_deposit(callback: CallbackQuery, state: FSMContext) -> None:
        if not check_admin_callback(callback):
            return
        
        deposit = int(callback.data.split(":", 1)[1])
        await state.update_data(deposit=deposit)
        await state.set_state(RentalStates.waiting_payment_method)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💵 Наличные", callback_data="payment:cash")],
            [InlineKeyboardButton(text="💳 Перевод", callback_data="payment:transfer")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_deposit")]
        ])
        await callback.message.edit_text("💳 Способ оплаты:", reply_markup=kb)
        await callback.answer()

    @router.callback_query(F.data.startswith("payment:"))
    async def cb_payment_method(callback: CallbackQuery, state: FSMContext) -> None:
        if not check_admin_callback(callback):
            return
        
        payment_method = callback.data.split(":", 1)[1]
        await state.update_data(payment_method=payment_method)
        await state.set_state(RentalStates.waiting_delivery_type)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚚 Доставка", callback_data="delivery:delivery")],
            [InlineKeyboardButton(text="🏠 Самовывоз", callback_data="delivery:pickup")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_payment")]
        ])
        await callback.message.edit_text("🚚 Доставка или самовывоз?", reply_markup=kb)
        await callback.answer()

    @router.callback_query(F.data.startswith("delivery:"))
    async def cb_delivery_type(callback: CallbackQuery, state: FSMContext) -> None:
        if not check_admin_callback(callback):
            return
        
        delivery_type = callback.data.split(":", 1)[1]
        await state.update_data(delivery_type=delivery_type)
        
        if delivery_type == "delivery":
            await state.set_state(RentalStates.waiting_address)
            await callback.message.edit_text("📍 Введите адрес доставки:")
            await callback.answer()
        else:
            # Самовывоз - адрес не нужен
            await state.update_data(address="Самовывоз")
            await create_rental_from_fsm(callback, state, scheduler)

    # --- Tool editing FSM handlers ---
    @router.message(EditToolStates.renaming, F.text)
    async def state_renaming(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
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
        if not await check_admin_access(message):
            return
        
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

    # --- Report FSM handlers ---
    @router.message(ReportStates.waiting_date, F.text)
    async def state_report_by_date(message: Message, state: FSMContext) -> None:
        if not await check_admin_access(message):
            return
        
        text = (message.text or "").strip()
        # Если пользователь нажал кнопку главного меню или ввёл команду — выходим из состояния и делегируем
        main_buttons = {"📋 Список аренд", "📊 Отчёт сейчас", "📅 Отчёт по дате", "📚 Каталог", "⬆️ Импорт CSV", "💵 Установить цену"}
        if text in main_buttons:
            await state.clear()
            # Роутинг на соответствующий сценарий
            if text == "📋 Список аренд":
                rows = await get_active_rentals(user_id=message.from_user.id)
                if not rows:
                    await message.answer("✅ Все инструменты возвращены. Активных аренд нет.")
                    return
                await message.answer("📋 Активные аренды (оставшееся время):", reply_markup=build_rentals_list_kb(rows))
                return
            if text == "📊 Отчёт сейчас":
                date = moscow_today_str()
                rows = await get_active_rentals(user_id=message.from_user.id)
                s = await sum_revenue_by_date_for_user(date, message.from_user.id)
                await message.answer(format_daily_report_with_revenue(date, rows, s))
                return
            if text == "📚 Каталог":
                items = await list_tools(limit=50)
                if not items:
                    await message.answer("Каталог пуст. Импортируйте CSV или установите цены командой /setprice.")
                    return
                await state.set_state(EditToolStates.choosing_tool)
                await message.answer("📚 Выберите инструмент для редактирования:", reply_markup=build_tools_list_kb(items))
                return
            if text == "⬆️ Импорт CSV":
                await message.answer(
                    "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\n"
                    "Формат: <code>Название,Цена</code> (две колонки).",
                    reply_markup=build_back_menu_kb(),
                )
                return
            if text == "💵 Установить цену":
                await message.answer(
                    "Используйте команду: /setprice &lt;название&gt; &lt;цена&gt;\nПример: /setprice Перфоратор Bosch 500"
                )
                return
            return
        if text.startswith("/"):
            await state.clear()
            # Простая подсказка после выхода из состояния: повторите команду
            await message.answer("Состояние сброшено. Повторите команду.")
            return
        if len(text) != 10 or text[4] != '-' or text[7] != '-':
            await message.answer("Формат: YYYY-MM-DD")
            return
        date = text
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(format_daily_report_with_revenue(date, rows, s), reply_markup=build_back_menu_kb())
        await state.clear()