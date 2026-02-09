## family-assistant-bot

Telegram-бот “семейный ассистент” с интеграцией DeepSeek.

### Запуск локально

1) Установить зависимости:

```bash
pip install -r requirements.txt
```

2) Создать `.env` (см. `.env.example`) и задать переменные окружения:

- **BOT_TOKEN**: токен Telegram бота
- **DEEPSEEK_API_KEY**: ключ DeepSeek

3) Запуск:

```bash
python bot.py
```

### Диагностика

- **/ping**: быстрый ответ “жив/не жив”
- **/diag**: сводка по конфигурации (без вывода секретов), хранилищу и аптайму
