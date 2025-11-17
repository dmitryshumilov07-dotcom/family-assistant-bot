from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio
import os
from user_profiles import UserManager
from deepseek_api import get_ai_response

# Инициализация менеджера пользователей
user_manager = UserManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    if not user_manager.is_user_allowed(update.message.from_user.id):
        await update.message.reply_text("⛔ Доступ запрещен.")
        return
    
    welcome_text = "Приветствую! Чем могу служить?"
    await update.message.reply_text(welcome_text)

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать ID пользователя (для настройки)"""
    await update.message.reply_text(f"Ваш ID: {update.message.from_user.id}")

async def add_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить в список покупок"""
    if not user_manager.is_user_allowed(update.message.from_user.id):
        await update.message.reply_text("⛔ Доступ запрещен.")
        return
    
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
    if not user_manager.is_user_allowed(update.message.from_user.id):
        await update.message.reply_text("⛔ Доступ запрещен.")
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

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений"""
    user_id = update.message.from_user.id
    
    # Проверка доступа
    if not user_manager.is_user_allowed(user_id):
        await update.message.reply_text("⛔ Доступ запрещен.")
        return
    
    # Получаем персонализированный ответ от AI
    response = await get_ai_response(update.message.text, user_id, user_manager)
    await update.message.reply_text(response)

def main():
    """Запуск бота"""
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("add", add_to_list))
    application.add_handler(CommandHandler("shopping", show_list))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
    
    # Запускаем бота
    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()