"""Admin access control functions."""
import os
from aiogram.types import Message


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом."""
    admin_ids = os.getenv("ADMIN_IDS", "").split(",")
    admin_ids = [int(uid.strip()) for uid in admin_ids if uid.strip()]
    return user_id in admin_ids


async def check_admin_access(message: Message) -> bool:
    """Проверяет доступ админа и отправляет сообщение, если доступ запрещён."""
    if not is_admin(message.from_user.id):
        admin_ids = os.getenv("ADMIN_IDS", "").split(",")
        admin_ids = [uid.strip() for uid in admin_ids if uid.strip()]
        admin_list = ", ".join(admin_ids) if admin_ids else "не настроены"
        
        await message.answer(
            "🚫 <b>Доступ запрещён</b>\n\n"
            "Этот бот доступен только администраторам.\n"
            f"ID администраторов: {admin_list}\n\n"
            "Обратитесь к администратору для получения доступа.",
            parse_mode="HTML"
        )
        return False
    return True


def check_admin_callback(callback) -> bool:
    """Проверяет доступ админа для callback запросов."""
    if not is_admin(callback.from_user.id):
        callback.answer("🚫 Доступ запрещён", show_alert=True)
        return False
    return True
