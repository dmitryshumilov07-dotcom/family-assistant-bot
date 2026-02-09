from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
import os
import sys
import platform
import logging
from datetime import datetime, timezone
from user_profiles import UserManager
from deepseek_api import get_ai_response

# Инициализация менеджера пользователей
user_manager = UserManager()

STARTED_AT = datetime.now(timezone.utc)

def _bool_env(key: str) -> bool:
    return bool(os.getenv(key))

async def _require_allowed(update: Update) -> bool:
    if not user_manager.is_user_allowed(update.message.from_user.id):
        await update.message.reply_text("⛔ Доступ запрещен.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    if not await _require_allowed(update):
        return
    user_manager.ensure_user(update.message.from_user)
    
    welcome_text = "Приветствую! Чем могу служить?"
    await update.message.reply_text(welcome_text)

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать ID пользователя (для настройки)"""
    await update.message.reply_text(f"Ваш ID: {update.message.from_user.id}")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрая проверка, что бот жив"""
    if not await _require_allowed(update):
        return
    uptime = datetime.now(timezone.utc) - STARTED_AT
    await update.message.reply_text(f"pong\nuptime: {str(uptime).split('.', 1)[0]}")

async def diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика конфигурации (без секретов)"""
    if not await _require_allowed(update):
        return

    data = user_manager._load_data()
    db_path = user_manager.db_path
    db_exists = os.path.exists(db_path)
    db_size = os.path.getsize(db_path) if db_exists else 0

    lines = [
        "diag:",
        f"- started_at_utc: {STARTED_AT.isoformat()}",
        f"- uptime: {str((datetime.now(timezone.utc) - STARTED_AT)).split('.', 1)[0]}",
        f"- python: {sys.version.split()[0]}",
        f"- platform: {platform.platform()}",
        f"- BOT_TOKEN: {'set' if _bool_env('BOT_TOKEN') else 'missing'}",
        f"- DEEPSEEK_API_KEY: {'set' if _bool_env('DEEPSEEK_API_KEY') else 'missing'}",
        f"- data_file: {db_path} ({'exists' if db_exists else 'missing'}, {db_size} bytes)",
        f"- users: {len(data.get('users', {}))}",
        f"- shopping_list_items: {len(data.get('shopping_list', []))}",
    ]

    await update.message.reply_text("\n".join(lines))

async def add_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить в список покупок"""
    if not await _require_allowed(update):
        return
    user_manager.ensure_user(update.message.from_user)
    
    try:
        # Получаем текст после команды /add
        item = update.message.text.split(' ', 1)[1].strip()
        
        # Загружаем текущие данные
        data = user_manager._load_data()
        
        # Добавляем item в список покупок
        if 'shopping_list' not in data:
            data['shopping_list'] = []
        
        data['shopping_list'].append(item)
        
        # Сохраняем обновленные данные
        user_manager._save_data(data)
        
        await update.message.reply_text(f"✅ Добавлено: {item}")
        
    except IndexError:
        await update.message.reply_text("❌ Использование: /add <предмет>")

async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список покупок"""
    if not await _require_allowed(update):
        return
    
    # Загружаем данные
    data = user_manager._load_data()
    
    # Получаем список покупок
    shopping_list = data.get('shopping_list', [])
    
    if not shopping_list:
        await update.message.reply_text("📝 Список покупок пуст")
    else:
        list_text = "🛒 Список покупок:\n\n" + "\n".join(f"• {item}" for item in shopping_list)
        await update.message.reply_text(list_text)

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очистить историю диалога"""
    if not await _require_allowed(update):
        return
    
    success = user_manager.clear_chat_history(update.message.from_user.id)
    if success:
        await update.message.reply_text("✅ История диалога очищена!")
    else:
        await update.message.reply_text("❌ Не удалось очистить историю")

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать историю диалога (для тестирования)"""
    if not await _require_allowed(update):
        return
    
    history = user_manager.get_chat_history(update.message.from_user.id)
    if not history:
        await update.message.reply_text("📝 История диалога пуста")
    else:
        history_text = "📋 История диалога:\n\n"
        for i, msg in enumerate(history[-5:], 1):  # Показываем последние 5 сообщений
            role = "👤 Вы" if msg["role"] == "user" else "🤖 Бот"
            history_text += f"{role}: {msg['content']}\n\n"
        await update.message.reply_text(history_text)

# НОВАЯ ВЕРСИЯ - ВСТАВИТЬ
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений"""
    user_id = update.message.from_user.id
    
    if not user_manager.is_user_allowed(user_id):
        await update.message.reply_text("⛔ Доступ запрещен.")
        return
    user_manager.ensure_user(update.message.from_user)
    
    # Показываем статус "печатает"
    async with update.message.chat.send_action(action=ChatAction.TYPING):
        response = await get_ai_response(update.message.text, user_id, user_manager)
    
    await update.message.reply_text(response)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception while handling update", exc_info=context.error)

def main():
    """Запуск бота"""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN не установлен (переменная окружения).")
        raise SystemExit(1)
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("diag", diag))
    application.add_handler(CommandHandler("add", add_to_list))
    application.add_handler(CommandHandler("shopping", show_list))
    application.add_handler(CommandHandler("clearhistory", clear_history))
    application.add_handler(CommandHandler("history", show_history))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))

    application.add_error_handler(on_error)
    
    logging.info("Bot starting (polling).")
    
    # Для Background Worker используем Polling
    application.run_polling()

if __name__ == "__main__":
    main()
    