"""Callback query handlers."""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from database import (
    get_active_rentals, renew_rental, close_rental, get_rental_by_id, 
    add_revenue, get_tool_by_id, update_tool_name, update_tool_price, 
    delete_tool, reset_database
)
from utils import format_remaining_time, format_local_end_time_hhmm, moscow_today_str
from .admin import check_admin_callback
from .keyboards import (
    build_main_menu, build_rentals_list_kb, build_rental_menu_kb,
    build_tools_list_kb, build_tool_menu_kb, build_back_menu_kb
)
from .fsm import EditToolStates


def register_callback_handlers(router: Router) -> None:
    """Регистрирует обработчики callback запросов."""
    
    @router.callback_query(F.data == "back_menu")
    async def cb_back_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not check_admin_callback(callback):
            return
        
        await state.clear()
        await callback.message.answer("Главное меню", reply_markup=build_main_menu())
        await callback.answer()

    @router.callback_query(F.data == "rentals_refresh")
    async def cb_rentals_refresh(callback: CallbackQuery) -> None:
        if not check_admin_callback(callback):
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
        if not check_admin_callback(callback):
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
        if not check_admin_callback(callback):
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
        if not check_admin_callback(callback):
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
                # Обновляем сообщение с новой информацией о времени
                left = format_remaining_time(int(row_after["start_time"]))
                end_hhmm = format_local_end_time_hhmm(int(row_after["start_time"]))
                
                deposit = int(row_after.get("deposit", 0))
                payment_method = row_after.get("payment_method", "cash")
                delivery_type = row_after.get("delivery_type", "pickup")
                address = row_after.get("address", "")
                
                payment_text = "💵 Наличные" if payment_method == "cash" else "💳 Перевод"
                delivery_text = "🚚 Доставка" if delivery_type == "delivery" else "🏠 Самовывоз"
                
                updated_text = (
                    f"🔧 <b>{row_after['tool_name']}</b> — {row_after['rent_price']}₽/сутки\n"
                    f"✅ Аренда продлена на 24 часа\n"
                    f"⏰ Осталось: {left} (до {end_hhmm})\n"
                    f"💰 Залог: {deposit}₽\n"
                    f"{payment_text}\n"
                    f"{delivery_text}"
                )
                if delivery_type == "delivery" and address:
                    updated_text += f"\n📍 Адрес: {address}"
                
                try:
                    await callback.message.edit_text(updated_text, reply_markup=build_rental_menu_kb(rental_id))
                except TelegramBadRequest:
                    # Если сообщение не изменилось, просто игнорируем ошибку
                    pass
            await callback.answer("Аренда продлена")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("Failed to renew rental %s", rental_id)
            try:
                await callback.answer("Ошибка продления", show_alert=True)
            except Exception:
                pass

    @router.callback_query(F.data.startswith("rental_close:"))
    async def cb_rental_close(callback: CallbackQuery) -> None:
        if not check_admin_callback(callback):
            return
        
        rental_id = int(callback.data.split(":", 1)[1])
        row = await get_rental_by_id(rental_id)
        if row:
            date_key = moscow_today_str()
            await add_revenue(date_key, rental_id, int(row["rent_price"]))
        await close_rental(rental_id)
        await callback.message.edit_text("🔒 Аренда инструмента завершена")
        await callback.answer()

    # --- Tool editing callbacks ---
    @router.callback_query(F.data == "tools_list")
    async def cb_tools_list(callback: CallbackQuery, state: FSMContext) -> None:
        if not check_admin_callback(callback):
            return
        
        items = await list_tools(limit=50)
        await state.set_state(EditToolStates.choosing_tool)
        await callback.message.edit_text("📚 Выберите инструмент для редактирования:")
        await callback.message.answer(reply_markup=build_tools_list_kb(items), text=" ")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_open:"))
    async def cb_tool_open(callback: CallbackQuery, state: FSMContext) -> None:
        if not check_admin_callback(callback):
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
        if not check_admin_callback(callback):
            return
        
        tool_id = int(callback.data.split(":", 1)[1])
        await state.update_data(tool_id=tool_id)
        await state.set_state(EditToolStates.renaming)
        await callback.message.edit_text("Введите новое название:")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_do_price:"))
    async def cb_tool_do_price(callback: CallbackQuery, state: FSMContext) -> None:
        if not check_admin_callback(callback):
            return
        
        tool_id = int(callback.data.split(":", 1)[1])
        await state.update_data(tool_id=tool_id)
        await state.set_state(EditToolStates.pricing)
        await callback.message.edit_text("Введите новую цену (число):")
        await callback.answer()

    @router.callback_query(F.data.startswith("tool_do_delete:"))
    async def cb_tool_do_delete(callback: CallbackQuery) -> None:
        if not check_admin_callback(callback):
            return
        
        tool_id = int(callback.data.split(":", 1)[1])
        await delete_tool(tool_id)
        await callback.message.edit_text("✅ Инструмент удалён")
        await callback.answer()

    # --- Reset database callback ---
    @router.callback_query(F.data == "reset_db_confirm")
    async def cb_reset_db(confirm: CallbackQuery) -> None:
        if not check_admin_callback(confirm):
            return
        
        await reset_database()
        await confirm.message.edit_text("✅ База очищена. Можно начинать заново.")
        await confirm.answer()

    # --- Legacy expiration callbacks ---
    @router.callback_query(F.data.startswith("renew:"))
    async def on_renew(callback: CallbackQuery) -> None:
        if not check_admin_callback(callback):
            return
        
        try:
            rental_id = int(callback.data.split(":", 1)[1])
            row_before = await get_rental_by_id(rental_id)
            if row_before:
                # Продлеваем без записи выручки (она уже записана при создании)
                await renew_rental(rental_id)
                row_after = await get_rental_by_id(rental_id)
                if row_after:
                    # Обновляем сообщение с новой информацией о времени
                    left = format_remaining_time(int(row_after["start_time"]))
                    end_hhmm = format_local_end_time_hhmm(int(row_after["start_time"]))
                    
                    deposit = int(row_after.get("deposit", 0))
                    payment_method = row_after.get("payment_method", "cash")
                    delivery_type = row_after.get("delivery_type", "pickup")
                    address = row_after.get("address", "")
                    
                    payment_text = "💵 Наличные" if payment_method == "cash" else "💳 Перевод"
                    delivery_text = "🚚 Доставка" if delivery_type == "delivery" else "🏠 Самовывоз"
                    
                    updated_text = (
                        f"🔧 <b>{row_after['tool_name']}</b> — {row_after['rent_price']}₽/сутки\n"
                        f"✅ Аренда продлена на 24 часа\n"
                        f"⏰ Осталось: {left} (до {end_hhmm})\n"
                        f"💰 Залог: {deposit}₽\n"
                        f"{payment_text}\n"
                        f"{delivery_text}"
                    )
                    if delivery_type == "delivery" and address:
                        updated_text += f"\n📍 Адрес: {address}"
                    
                    try:
                        await callback.message.edit_text(updated_text, reply_markup=build_rental_menu_kb(rental_id))
                    except TelegramBadRequest:
                        # Если сообщение не изменилось, просто игнорируем ошибку
                        pass
                await callback.answer("Аренда продлена")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("Failed to renew rental %s", rental_id)
            try:
                await callback.answer("Ошибка продления", show_alert=True)
            except Exception:
                pass

    @router.callback_query(F.data.startswith("close:"))
    async def on_close(callback: CallbackQuery) -> None:
        if not check_admin_callback(callback):
            return
        
        rental_id = int(callback.data.split(":", 1)[1])
        row = await get_rental_by_id(rental_id)
        if row:
            date_key = moscow_today_str()
            await add_revenue(date_key, rental_id, int(row["rent_price"]))
        await close_rental(rental_id)
        await callback.message.edit_text("🔒 Аренда инструмента завершена")
        await callback.answer()