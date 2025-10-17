"""Keyboard builders."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from utils import format_remaining_time


def build_main_menu() -> ReplyKeyboardMarkup:
    """Создает главное меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Список аренд"), KeyboardButton(text="📊 Отчёт сейчас")],
            [KeyboardButton(text="📅 Отчёт по дате"), KeyboardButton(text="📚 Каталог")],
            [KeyboardButton(text="⬆️ Импорт CSV"), KeyboardButton(text="💵 Установить цену")],
        ],
        resize_keyboard=True,
    )


def build_rentals_list_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    """Создает клавиатуру списка аренд."""
    buttons = []
    for r in rows:
        left = format_remaining_time(int(r["start_time"]))
        buttons.append([InlineKeyboardButton(text=f"{r['tool_name']} — {left}", callback_data=f"rental_open:{r['id']}")])
    buttons.append([InlineKeyboardButton(text="Обновить", callback_data="rentals_refresh")])
    buttons.append([InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_rental_menu_kb(rental_id: int) -> InlineKeyboardMarkup:
    """Создает меню для конкретной аренды."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продлить на 24ч", callback_data=f"rental_renew:{rental_id}")],
        [InlineKeyboardButton(text="🔒 Завершить сейчас", callback_data=f"rental_close:{rental_id}")],
        [InlineKeyboardButton(text="↩️ К списку", callback_data="rentals_list")],
    ])


def build_tools_list_kb(items: list[dict]) -> InlineKeyboardMarkup:
    """Создает клавиатуру списка инструментов."""
    rows = []
    for it in items:
        rows.append([InlineKeyboardButton(text=f"{it['name']} ({it['price']}₽)", callback_data=f"tool_open:{it['id']}")])
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_tool_menu_kb(tool_id: int) -> InlineKeyboardMarkup:
    """Создает меню для инструмента."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"tool_do_rename:{tool_id}")],
        [InlineKeyboardButton(text="💵 Изменить цену", callback_data=f"tool_do_price:{tool_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"tool_do_delete:{tool_id}")],
        [InlineKeyboardButton(text="↩️ К списку", callback_data="tools_list")],
    ])


def build_expiration_keyboard(rental_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для уведомления об истечении аренды."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Продлить аренду", callback_data=f"renew:{rental_id}"),
            InlineKeyboardButton(text="❌ Забрал инструмент", callback_data=f"close:{rental_id}"),
        ]
    ])


def build_back_menu_kb() -> InlineKeyboardMarkup:
    """Создает кнопку возврата в меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ В меню", callback_data="back_menu")]
    ])


def build_reset_confirm_kb() -> InlineKeyboardMarkup:
    """Создает клавиатуру подтверждения сброса БД."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ Да, очистить всё", callback_data="reset_db_confirm"),
            InlineKeyboardButton(text="Отмена", callback_data="back_menu"),
        ]
    ])
