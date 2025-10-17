import logging
import os
from typing import Tuple

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from database import add_rental, get_active_rentals, renew_rental, close_rental, get_rental_by_id, add_revenue, sum_revenue_by_date_for_user, get_tool_by_name, upsert_tool, list_tools, import_catalog_from_csv, reset_database, get_tool_by_id, update_tool_name, update_tool_price, delete_tool, sum_revenue_by_date, reset_rental_start_now
from utils import parse_tool_and_price, format_active_list, ts_to_moscow_date_str, moscow_today_str, moscow_yesterday_str, format_remaining_time, format_local_end_time_hhmm

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    admin_ids = os.getenv("ADMIN_IDS", "").split(",")
    admin_ids = [int(uid.strip()) for uid in admin_ids if uid.strip()]
    return user_id in admin_ids


def check_admin_access(message: Message) -> bool:
    """Проверяет доступ админа и отправляет сообщение, если доступ запрещён"""
    if not is_admin(message.from_user.id):
        admin_ids = os.getenv("ADMIN_IDS", "").split(",")
        admin_ids = [uid.strip() for uid in admin_ids if uid.strip()]
        admin_list = ", ".join(admin_ids) if admin_ids else "не настроены"
        
        message.answer(
            "🚫 <b>Доступ запрещён</b>\n\n"
            "Этот бот доступен только администраторам.\n"
            f"ID администраторов: {admin_list}\n\n"
            "Обратитесь к администратору для получения доступа.",
            parse_mode="HTML"
        )
        return False
    return True


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


class RentalStates(StatesGroup):
    waiting_deposit = State()
    waiting_payment_method = State()
    waiting_delivery_type = State()
    waiting_address = State()


def register_handlers(dp, scheduler) -> None:
    router = Router()
    fsm_router = Router()  # Отдельный роутер для FSM обработчиков

    def build_main_menu() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📋 Список аренд"), KeyboardButton(text="📊 Отчёт сейчас")],
                [KeyboardButton(text="📅 Отчёт по дате"), KeyboardButton(text="📚 Каталог")],
                [KeyboardButton(text="⬆️ Импорт CSV"), KeyboardButton(text="💵 Установить цену")],
            ],
            resize_keyboard=True,
        )

    # --- FSM handlers for rental creation ---
    @fsm_router.message(RentalStates.waiting_deposit, F.text)
    async def state_waiting_deposit(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
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
                from utils import format_daily_report_with_revenue
                date = moscow_today_str()
                rows = await get_active_rentals(user_id=message.from_user.id)
                s = await sum_revenue_by_date_for_user(date, message.from_user.id)
                await message.answer(format_daily_report_with_revenue(date, rows, s))
                return
            if text == "📚 Каталог":
                await cmd_catalog(message, state)
                return
            if text == "⬆️ Импорт CSV":
                back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]])
                await message.answer(
                    "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\n"
                    "Формат: <code>Название,Цена</code> (две колонки).",
                    reply_markup=back_kb,
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

    @fsm_router.message(RentalStates.waiting_address, F.text)
    async def state_waiting_address(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
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
                from utils import format_daily_report_with_revenue
                date = moscow_today_str()
                rows = await get_active_rentals(user_id=message.from_user.id)
                s = await sum_revenue_by_date_for_user(date, message.from_user.id)
                await message.answer(format_daily_report_with_revenue(date, rows, s))
                return
            if text == "📚 Каталог":
                await cmd_catalog(message, state)
                return
            if text == "⬆️ Импорт CSV":
                back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]])
                await message.answer(
                    "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\n"
                    "Формат: <code>Название,Цена</code> (две колонки).",
                    reply_markup=back_kb,
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
        await create_rental_from_fsm(message, state)

    # --- FSM callback handlers ---
    @fsm_router.callback_query(F.data.startswith("deposit:"))
    async def cb_deposit(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
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

    @fsm_router.callback_query(F.data == "back_to_deposit")
    async def cb_back_to_deposit(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        data = await state.get_data()
        tool_name = data.get("tool_name")
        rent_price = data.get("rent_price")
        
        await state.set_state(RentalStates.waiting_deposit)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Без залога", callback_data="deposit:0")],
            [InlineKeyboardButton(text="↩️ Отмена", callback_data="back_menu")]
        ])
        await callback.message.edit_text(
            f"🔧 <b>{tool_name}</b> — {rent_price}₽/сутки\n\n"
            "💰 Какой залог оставили? (введите сумму или нажмите кнопку)",
            reply_markup=kb
        )
        await callback.answer()

    @fsm_router.callback_query(F.data == "back_to_payment")
    async def cb_back_to_payment(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        data = await state.get_data()
        tool_name = data.get("tool_name")
        rent_price = data.get("rent_price")
        deposit = data.get("deposit", 0)
        
        await state.set_state(RentalStates.waiting_payment_method)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💵 Наличные", callback_data="payment:cash")],
            [InlineKeyboardButton(text="💳 Перевод", callback_data="payment:transfer")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_deposit")]
        ])
        await callback.message.edit_text(
            f"🔧 <b>{tool_name}</b> — {rent_price}₽/сутки\n"
            f"💰 Залог: {deposit}₽\n\n"
            "💳 Способ оплаты:",
            reply_markup=kb
        )
        await callback.answer()

    @fsm_router.callback_query(F.data == "back_to_delivery")
    async def cb_back_to_delivery(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        data = await state.get_data()
        tool_name = data.get("tool_name")
        rent_price = data.get("rent_price")
        deposit = data.get("deposit", 0)
        payment_method = data.get("payment_method", "cash")
        
        payment_text = "💵 Наличные" if payment_method == "cash" else "💳 Перевод"
        
        await state.set_state(RentalStates.waiting_delivery_type)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚚 Доставка", callback_data="delivery:delivery")],
            [InlineKeyboardButton(text="🏠 Самовывоз", callback_data="delivery:pickup")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_payment")]
        ])
        await callback.message.edit_text(
            f"🔧 <b>{tool_name}</b> — {rent_price}₽/сутки\n"
            f"💰 Залог: {deposit}₽\n"
            f"{payment_text}\n\n"
            "🚚 Доставка или самовывоз?",
            reply_markup=kb
        )
        await callback.answer()

    @fsm_router.callback_query(F.data.startswith("payment:"))
    async def cb_payment_method(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
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

    @fsm_router.callback_query(F.data.startswith("delivery:"))
    async def cb_delivery_type(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
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
            await create_rental_from_fsm(callback, state)

    @router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
            return
        
        await state.clear()
        main_kb = build_main_menu()
        await message.answer(
            "👋 Привет! Отправь сообщение в формате: \n\n"
            "<b>Перфоратор Bosch 500</b>\n\n"
            "Где последняя цифра — цена в ₽ за сутки.",
            reply_markup=main_kb,
        )

    def build_rentals_list_kb(rows: list[dict]) -> InlineKeyboardMarkup:
        buttons = []
        for r in rows:
            left = format_remaining_time(int(r["start_time"]))
            buttons.append([InlineKeyboardButton(text=f"{r['tool_name']} — {left}", callback_data=f"rental_open:{r['id']}")])
        buttons.append([InlineKeyboardButton(text="Обновить", callback_data="rentals_refresh")])
        buttons.append([InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    def build_rental_menu_kb(rental_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Продлить на 24ч", callback_data=f"rental_renew:{rental_id}")],
            [InlineKeyboardButton(text="🔒 Завершить сейчас", callback_data=f"rental_close:{rental_id}")],
            [InlineKeyboardButton(text="↩️ К списку", callback_data="rentals_list")],
        ])

    @router.message(Command("list"))
    async def cmd_list(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
            return
        
        await state.clear()
        rows = await get_active_rentals(user_id=message.from_user.id)
        if not rows:
            await message.answer("✅ Все инструменты возвращены. Активных аренд нет.")
            return
        await message.answer("📋 Активные аренды (оставшееся время):", reply_markup=build_rentals_list_kb(rows))

    @router.message(Command("report_now"))
    async def cmd_report_now(message: Message) -> None:
        if not check_admin_access(message):
            return
        
        await scheduler.send_daily_report_for_user(message.from_user.id)
        await message.answer("✅ Отчёт отправлен")

    @router.message(Command("report_today"))
    async def cmd_report_today(message: Message) -> None:
        if not check_admin_access(message):
            return
        
        date = moscow_today_str()
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        from utils import format_daily_report_with_revenue
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(Command("report"))
    async def cmd_report(message: Message) -> None:
        if not check_admin_access(message):
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
        from utils import format_daily_report_with_revenue
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(Command("expire_last"))
    async def cmd_expire_last(message: Message) -> None:
        if not check_admin_access(message):
            return
        
        rows = await get_active_rentals(user_id=message.from_user.id)
        if not rows:
            await message.answer("✅ Активных аренд нет")
            return
        rental_id = int(rows[0]["id"])  # последняя по ORDER BY id DESC в БД — мы берём первую из списка /list
        await scheduler.trigger_expiration_now(rental_id)
        await message.answer("⏱️ Тестовое уведомление отправлено")

    @router.message(Command("income_today"))
    async def cmd_income_today(message: Message) -> None:
        if not check_admin_access(message):
            return
        
        date = moscow_today_str()
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(f"💰 Доход за {date}: {s}₽")

    @router.message(Command("income"))
    async def cmd_income(message: Message) -> None:
        if not check_admin_access(message):
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
        if not check_admin_access(message):
            return
        
        items = await list_tools(limit=50)
        if not items:
            await message.answer("Каталог пуст. Импортируйте CSV или установите цены командой /setprice.")
            return
        await state.set_state(EditToolStates.choosing_tool)
        await message.answer("📚 Выберите инструмент для редактирования:", reply_markup=build_tools_list_kb(items))

    @router.message(Command("setprice"))
    async def cmd_setprice(message: Message) -> None:
        if not check_admin_access(message):
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

    @router.callback_query(F.data == "tools_list")
    async def cb_tools_list(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        items = await list_tools(limit=50)
        await state.set_state(EditToolStates.choosing_tool)
        await callback.message.edit_text("📚 Выберите инструмент для редактирования:")
        await callback.message.answer(reply_markup=build_tools_list_kb(items), text=" ")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_open:"))
    async def cb_tool_open(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
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
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        tool_id = int(callback.data.split(":", 1)[1])
        await state.update_data(tool_id=tool_id)
        await state.set_state(EditToolStates.renaming)
        await callback.message.edit_text("Введите новое название:")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_do_price:"))
    async def cb_tool_do_price(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        tool_id = int(callback.data.split(":", 1)[1])
        await state.update_data(tool_id=tool_id)
        await state.set_state(EditToolStates.pricing)
        await callback.message.edit_text("Введите новую цену (число):")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_do_delete:"))
    async def cb_tool_do_delete(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        tool_id = int(callback.data.split(":", 1)[1])
        await delete_tool(tool_id)
        await callback.message.edit_text("✅ Инструмент удалён")
        await callback.answer()

    @router.message(EditToolStates.renaming, F.text)
    async def state_renaming(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
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
        if not check_admin_access(message):
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

    @router.message(Command("import_catalog"))
    async def cmd_import_catalog(message: Message) -> None:
        if not check_admin_access(message):
            return
        
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
    async def btn_list(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
            return
        
        await state.clear()
        rows = await get_active_rentals(user_id=message.from_user.id)
        if not rows:
            await message.answer("✅ Все инструменты возвращены. Активных аренд нет.")
            return
        await message.answer("📋 Активные аренды (оставшееся время):", reply_markup=build_rentals_list_kb(rows))

    @router.callback_query(F.data == "rentals_refresh")
    async def cb_rentals_refresh(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        rows = await get_active_rentals(user_id=callback.from_user.id)
        if not rows:
            await callback.message.edit_text("✅ Все инструменты возвращены. Активных аренд нет.", reply_markup=None)
            await callback.answer()
            return
        # Меняем сразу и текст, и клавиатуру у того же сообщения
        await callback.message.edit_text("📋 Активные аренды (оставшееся время):")
        await callback.message.edit_reply_markup(reply_markup=build_rentals_list_kb(rows))
        await callback.answer("Обновлено")

    @router.callback_query(F.data == "rentals_list")
    async def cb_rentals_list(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        rows = await get_active_rentals(user_id=callback.from_user.id)
        if not rows:
            await callback.message.edit_text("✅ Все инструменты возвращены. Активных аренд нет.")
            await callback.answer()
            return
        await callback.message.edit_text(
            "📋 Активные аренды (оставшееся время):",
            reply_markup=build_rentals_list_kb(rows),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("rental_open:"))
    async def cb_rental_open(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        rental_id = int(callback.data.split(":", 1)[1])
        row = await get_rental_by_id(rental_id)
        if not row or int(row.get("active", 0)) != 1:
            await callback.answer("Аренда неактивна", show_alert=True)
            return
        left = format_remaining_time(int(row["start_time"]))
        end_hhmm = format_local_end_time_hhmm(int(row["start_time"]))
        
        # Формируем детальную информацию
        deposit = int(row.get("deposit", 0))
        payment_method = row.get("payment_method", "cash")
        delivery_type = row.get("delivery_type", "pickup")
        address = row.get("address", "")
        
        payment_text = "💵 Наличные" if payment_method == "cash" else "💳 Перевод"
        delivery_text = "🚚 Доставка" if delivery_type == "delivery" else "🏠 Самовывоз"
        
        text = (
            f"🔧 <b>{row['tool_name']}</b> — {row['rent_price']}₽/сутки\n"
            f"⏰ Осталось: {left} (до {end_hhmm})\n"
            f"💰 Залог: {deposit}₽\n"
            f"{payment_text}\n"
            f"{delivery_text}"
        )
        
        if delivery_type == "delivery" and address:
            text += f"\n📍 Адрес: {address}"
        
        await callback.message.edit_text(text, reply_markup=build_rental_menu_kb(rental_id))
        await callback.answer()

    @router.callback_query(F.data.startswith("rental_renew:"))
    async def cb_rental_renew(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        try:
            rental_id = int(callback.data.split(":", 1)[1])
            row_before = await get_rental_by_id(rental_id)
            if not row_before:
                await callback.answer("Аренда не найдена", show_alert=True)
                return
            # Начислим выручку за период и продлим на +24ч от текущего дедлайна
            date_key = moscow_today_str()
            await add_revenue(date_key, rental_id, int(row_before["rent_price"]))
            await renew_rental(rental_id)
            row_after = await get_rental_by_id(rental_id)
            if row_after:
                await scheduler.schedule_expiration_notification(
                    rental_id=rental_id,
                    start_time_ts=int(row_after["start_time"]),
                    user_id=row_after["user_id"],
                    tool_name=row_after["tool_name"],
                )
                left = format_remaining_time(int(row_after["start_time"]))
                end_hhmm = format_local_end_time_hhmm(int(row_after["start_time"]))
                text = f"🔧 {row_after['tool_name']} — {row_after['rent_price']}₽/сутки\nОсталось: {left} (до {end_hhmm})"
                # Пытаемся обновить текст; если Telegram вернёт "message is not modified" — игнорируем
                try:
                    await callback.message.edit_text(text, reply_markup=build_rental_menu_kb(rental_id))
                except Exception:
                    pass
            await callback.answer("✅ Продлено на 24ч")
        except Exception as e:
            logger.exception("rental_renew failed: %s", e)
            try:
                await callback.answer("Ошибка продления", show_alert=True)
            except Exception:
                pass

    @router.callback_query(F.data.startswith("rental_close:"))
    async def cb_rental_close(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        rental_id = int(callback.data.split(":", 1)[1])
        row = await get_rental_by_id(rental_id)
        if row:
            date_key = moscow_today_str()
            await add_revenue(date_key, rental_id, int(row["rent_price"]))
        await close_rental(rental_id)
        await callback.message.edit_text("🔒 Аренда инструмента завершена")
        await callback.answer()

    @router.message(F.text == "📅 Отчёт по дате")
    async def btn_report_by_date(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
            return
        
        await state.set_state(ReportStates.waiting_date)
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]
        ])
        await message.answer("Введите дату в формате YYYY-MM-DD", reply_markup=back_kb)

    @router.message(F.text == "📊 Отчёт сейчас")
    async def btn_report_now(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
            return
        
        await state.clear()
        # То же, что /report_today
        from utils import format_daily_report_with_revenue
        date = moscow_today_str()
        rows = await get_active_rentals(user_id=message.from_user.id)
        s = await sum_revenue_by_date_for_user(date, message.from_user.id)
        await message.answer(format_daily_report_with_revenue(date, rows, s))

    @router.message(ReportStates.waiting_date, F.text)
    async def state_report_by_date(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
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
                from utils import format_daily_report_with_revenue
                date = moscow_today_str()
                rows = await get_active_rentals(user_id=message.from_user.id)
                s = await sum_revenue_by_date_for_user(date, message.from_user.id)
                await message.answer(format_daily_report_with_revenue(date, rows, s))
                return
            if text == "📚 Каталог":
                await cmd_catalog(message, state)
                return
            if text == "⬆️ Импорт CSV":
                back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]])
                await message.answer(
                    "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\nФормат: <code>Название,Цена</code> (две колонки).",
                    reply_markup=back_kb,
                )
                return
            if text == "💵 Установить цену":
                await message.answer(
                    "Используйте команду: /setprice &lt;название&gt; &lt;цена&gt;\nПример: /setprice Перфоратор Bosch 500"
                )
                return
        if text.startswith("/"):
            await state.clear()
            # Простая подсказка после выхода из состояния: повторите команду
            await message.answer("Состояние сброшено. Повторите команду.")
            return
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
        if not check_admin_access(message):
            return
        
        # Сбросим возможное состояние отчёта по дате, чтобы текст кнопки не обрабатывался как дата
        await state.clear()
        # Переиспользуем тот же сценарий FSM, что и команда /catalog
        await cmd_catalog(message, state)

    @router.message(F.text == "⬆️ Импорт CSV")
    async def btn_import_hint(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
            return
        
        await state.clear()
        back_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]
        ])
        await message.answer(
            "Отправьте CSV-файл (UTF-8) прямо в чат — бот импортирует каталог.\n"
            "Формат: <code>Название,Цена</code> (две колонки).",
            reply_markup=back_kb,
        )

    @router.callback_query(F.data == "back_menu")
    async def cb_back_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        await state.clear()
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
        if not is_admin(confirm.from_user.id):
            await confirm.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        await reset_database()
        await confirm.message.edit_text("✅ База очищена. Можно начинать заново.")
        await confirm.answer()

    @router.message(F.text == "💵 Установить цену")
    async def btn_setprice_hint(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
            return
        
        await state.clear()
        await message.answer(
            "Используйте команду: /setprice &lt;название&gt; &lt;цена&gt;\n"
            "Пример: /setprice Перфоратор Bosch 500"
        )

    # --- CSV import by sending a file ---
    @router.message(F.document)
    async def on_document(message: Message) -> None:
        if not check_admin_access(message):
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
    async def add_rent_handler(message: Message, state: FSMContext) -> None:
        if not check_admin_access(message):
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


    async def create_rental_from_fsm(message_or_callback, state: FSMContext) -> None:
        """Создает аренду из данных FSM"""
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
        await scheduler.schedule_expiration_notification(
            rental_id=rental_id,
            start_time_ts=(await get_rental_by_id(rental_id))["start_time"],
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

    @router.callback_query(F.data.startswith("renew:"))
    async def on_renew(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        rental_id = int(callback.data.split(":", 1)[1])
        row_before = await get_rental_by_id(rental_id)
        if row_before:
            # Продлеваем без записи выручки (она уже записана при создании)
            await renew_rental(rental_id)
            row_after = await get_rental_by_id(rental_id)
            if row_after:
                await scheduler.schedule_expiration_notification(
                    rental_id=rental_id,
                    start_time_ts=row_after["start_time"],
                    user_id=row_after["user_id"],
                    tool_name=row_after["tool_name"],
                )
        await callback.message.edit_text("✅ Аренда продлена на 24 часа")
        await callback.answer()

    @router.callback_query(F.data.startswith("close:"))
    async def on_close(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("🚫 Доступ запрещён", show_alert=True)
            return
        
        rental_id = int(callback.data.split(":", 1)[1])
        # Закрываем без записи выручки (она уже записана при создании)
        await close_rental(rental_id)
        await callback.message.edit_text("🔒 Аренда инструмента завершена")
        await callback.answer()

    # Регистрируем FSM роутер первым, чтобы он имел приоритет
    dp.include_router(fsm_router)
    dp.include_router(router)


