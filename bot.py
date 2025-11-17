import telebot
import asyncio
import os
from user_profiles import UserManager
from deepseek_api import get_ai_response

# Инициализация бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# Инициализация менеджера пользователей
user_manager = UserManager()

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Обработчик команды /start"""
    if not user_manager.is_user_allowed(message.from_user.id):
        bot.reply_to(message, "⛔ Доступ запрещен.")
        return
    
    welcome_text = "Приветствую! Чем могу служить?"
    bot.reply_to(message, welcome_text)

@bot.message_handler(commands=['myid'])
def get_my_id(message):
    """Показать ID пользователя (для настройки)"""
    bot.reply_to(message, f"Ваш ID: {message.from_user.id}")

@bot.message_handler(commands=['add'])
def add_to_list(message):
    """Добавить в список покупок"""
    if not user_manager.is_user_allowed(message.from_user.id):
        bot.reply_to(message, "⛔ Доступ запрещен.")
        return
    
    try:
        # Получаем текст после команды /add
        item = message.text.split(' ', 1)[1].strip()
        
        # Загружаем текущие данные
        data = user_manager._load_data()
        
        # Добавляем item в список покупок
        if 'shopping_list' not in data:
            data['shopping_list'] = []
        
        data['shopping_list'].append(item)
        
        # Сохраняем обновленные данные
        user_manager._save_data(data)
        
        bot.reply_to(message, f"✅ Добавлено: {item}")
        
    except IndexError:
        bot.reply_to(message, "❌ Использование: /add <предмет>")

@bot.message_handler(commands=['shopping'])
def show_list(message):
    """Показать список покупок"""
    if not user_manager.is_user_allowed(message.from_user.id):
        bot.reply_to(message, "⛔ Доступ запрещен.")
        return
    
    # Загружаем данные
    data = user_manager._load_data()
    
    # Получаем список покупок
    shopping_list = data.get('shopping_list', [])
    
    if not shopping_list:
        bot.reply_to(message, "📝 Список покупок пуст")
    else:
        list_text = "🛒 Список покупок:\n\n" + "\n".join(f"• {item}" for item in shopping_list)
        bot.reply_to(message, list_text)

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Обработка всех сообщений"""
    user_id = message.from_user.id
    
    # Проверка доступа
    if not user_manager.is_user_allowed(user_id):
        bot.reply_to(message, "⛔ Доступ запрещен.")
        return
    
    # Получаем персонализированный ответ от AI
    response = asyncio.run(get_ai_response(message.text, user_id, user_manager))
    bot.reply_to(message, response)

if __name__ == "__main__":
    print("Бот запущен...")
    bot.polling()