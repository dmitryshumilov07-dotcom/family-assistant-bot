import os
from dotenv import load_dotenv

load_dotenv()

# Конфигурация бота
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

# Настройки DeepSeek API
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# Список разрешенных пользователей (ID членов семьи)
ALLOWED_USERS = []  # Заполним позже

# Контекст ассистента
ASSISTANT_CONTEXT = """
Ты - семейный ассистент для домашней группы. Твои задачи:
1. Помогать с планированием дел по дому
2. Напоминать о важных событиях
3. Предлагать решения бытовых вопросов
4. Помогать с составлением списков покупок
5. Отвечать на вопросы о домашних делах

Будь добрым, внимательным и полезным. Обращайся ко всем уважительно.
"""