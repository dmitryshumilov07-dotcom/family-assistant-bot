import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from deepseek_api import assistant
from database import db

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Получаем токен из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Разрешенные пользователи (пока пусто - разрешены все)
ALLOWED_USERS = []

def is_user_allowed(user_id):
    """Проверяем, разрешен ли пользователь"""
    return user_id in ALLOWED_USERS or not ALLOWED_USERS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    
    if not is_user_allowed(user_id):
        await update.message.reply_text("Извините, у вас нет доступа к этому боту.")
        return
    
    welcome_text = """
👋 Привет! Я ваш семейный ассистент!

Я могу:
• Ответить на любые ваши вопросы
• Помочь с планированием дел
• Добавить items в список покупок
• Напомнить о важных делах

Просто напишите мне что-нибудь!
    """
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    user_message = update.message.text
    
    if not is_user_allowed(user_id):
        await update.message.reply_text("Извините, у вас нет доступа к этому боту.")
        return
    
    # Показываем, что бот печатает
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Получаем ответ от AI
    response = assistant.ask_assistant(user_message, user_id)
    
    # Отправляем ответ
    await update.message.reply_text(response)

async def shopping_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список покупок"""
    user_id = update.effective_user.id
    
    if not is_user_allowed(user_id):
        await update.message.reply_text("Извините, у вас нет доступа к этому боту.")
        return
    
    items = db.get_shopping_list()
    if not items:
        await update.message.reply_text("📝 Список покупок пуст!")
        return
    
    list_text = "📝 Список покупок:\n\n"
    for item in items:
        status = "✅" if item['completed'] else "◻️"
        list_text += f"{status} {item['item']}\n"
    
    await update.message.reply_text(list_text)

async def add_to_shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить item в список покупок"""
    user_id = update.effective_user.id
    
    if not is_user_allowed(user_id):
        await update.message.reply_text("Извините, у вас нет доступа к этому боту.")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /add <item>")
        return
    
    item = " ".join(context.args)
    db.add_to_shopping_list(item, user_id)
    await update.message.reply_text(f"✅ Добавлено в список покупок: {item}")

def main():
    """Запуск бота"""
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN не установлен!")
        return
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("shopping", shopping_list))
    application.add_handler(CommandHandler("add", add_to_shopping))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бота
    logging.info("Бот запущен!")
    application.run_polling()

if __name__ == '__main__':
    main()