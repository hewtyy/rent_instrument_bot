"""Main bot handlers registration."""
from aiogram import Dispatcher

from handlers import register_fsm_handlers, register_command_handlers, register_callback_handlers


def register_handlers(dp: Dispatcher, scheduler) -> None:
    """Регистрирует все обработчики бота."""
    from aiogram import Router
    
    # Создаем основной роутер
    router = Router()
    
    # Регистрируем FSM обработчики первыми для приоритета
    register_fsm_handlers(router, scheduler)
    
    # Регистрируем команды и callback обработчики
    register_command_handlers(router)
    register_callback_handlers(router)
    
    # Включаем роутер в диспетчер
    dp.include_router(router)