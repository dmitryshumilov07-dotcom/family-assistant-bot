#!/usr/bin/env python3
"""
Diagnostic tool for Family Assistant Bot
Checks system health, API connectivity, and configuration
"""

import os
import sys
import json
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path

class BotDiagnostics:
    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "checks": {},
            "errors": [],
            "warnings": []
        }
    
    def check_environment_variables(self):
        """Проверка переменных окружения"""
        print("🔍 Проверка переменных окружения...")
        
        required_vars = {
            "BOT_TOKEN": "Telegram Bot Token",
            "DEEPSEEK_API_KEY": "DeepSeek API Key"
        }
        
        for var, description in required_vars.items():
            value = os.getenv(var)
            if value:
                masked_value = value[:10] + "..." if len(value) > 10 else "***"
                print(f"  ✅ {var}: {masked_value}")
                self.results["checks"][var] = "OK"
            else:
                print(f"  ❌ {var}: НЕ УСТАНОВЛЕНА")
                self.results["checks"][var] = "MISSING"
                self.results["errors"].append(f"{var} не установлена")
    
    def check_files(self):
        """Проверка наличия необходимых файлов"""
        print("\n🔍 Проверка файлов проекта...")
        
        required_files = [
            "bot.py",
            "config.py", 
            "deepseek_api.py",
            "user_profiles.py",
            "requirements.txt"
        ]
        
        for filename in required_files:
            filepath = Path(filename)
            if filepath.exists():
                size = filepath.stat().st_size
                print(f"  ✅ {filename} ({size} bytes)")
                self.results["checks"][f"file_{filename}"] = "OK"
            else:
                print(f"  ❌ {filename} НЕ НАЙДЕН")
                self.results["checks"][f"file_{filename}"] = "MISSING"
                self.results["errors"].append(f"Файл {filename} не найден")
    
    def check_database(self):
        """Проверка базы данных"""
        print("\n🔍 Проверка базы данных...")
        
        db_path = Path("family_data.json")
        
        if db_path.exists():
            try:
                with open(db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                print(f"  ✅ База данных найдена")
                print(f"  📊 Пользователей: {len(data.get('users', {}))}")
                print(f"  📊 Позиций в списке покупок: {len(data.get('shopping_list', []))}")
                
                # Проверка истории чата
                total_messages = 0
                for user_id, user_data in data.get('users', {}).items():
                    history_count = len(user_data.get('chat_history', []))
                    total_messages += history_count
                    print(f"  📨 Пользователь {user_id}: {history_count} сообщений в истории")
                
                self.results["checks"]["database"] = "OK"
                self.results["checks"]["database_users"] = len(data.get('users', {}))
                self.results["checks"]["database_messages"] = total_messages
                
            except json.JSONDecodeError as e:
                print(f"  ❌ Ошибка чтения БД: {e}")
                self.results["errors"].append(f"База данных повреждена: {e}")
                self.results["checks"]["database"] = "CORRUPTED"
        else:
            print("  ⚠️  База данных не найдена (будет создана при первом запуске)")
            self.results["checks"]["database"] = "NOT_FOUND"
            self.results["warnings"].append("База данных не существует")
    
    async def check_telegram_api(self):
        """Проверка подключения к Telegram API"""
        print("\n🔍 Проверка Telegram API...")
        
        token = os.getenv("BOT_TOKEN")
        if not token:
            print("  ❌ BOT_TOKEN не установлен")
            self.results["checks"]["telegram_api"] = "NO_TOKEN"
            return
        
        try:
            url = f"https://api.telegram.org/bot{token}/getMe"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("ok"):
                            bot_info = data.get("result", {})
                            print(f"  ✅ Telegram API доступен")
                            print(f"  🤖 Бот: @{bot_info.get('username')}")
                            print(f"  📛 Имя: {bot_info.get('first_name')}")
                            self.results["checks"]["telegram_api"] = "OK"
                            self.results["checks"]["bot_username"] = bot_info.get("username")
                        else:
                            print(f"  ❌ Ошибка ответа: {data}")
                            self.results["checks"]["telegram_api"] = "ERROR"
                            self.results["errors"].append("Telegram API вернул ошибку")
                    else:
                        print(f"  ❌ HTTP {response.status}")
                        self.results["checks"]["telegram_api"] = f"HTTP_{response.status}"
                        self.results["errors"].append(f"Telegram API HTTP {response.status}")
        except asyncio.TimeoutError:
            print("  ❌ Таймаут подключения")
            self.results["checks"]["telegram_api"] = "TIMEOUT"
            self.results["errors"].append("Telegram API таймаут")
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")
            self.results["checks"]["telegram_api"] = "ERROR"
            self.results["errors"].append(f"Telegram API: {e}")
    
    async def check_deepseek_api(self):
        """Проверка подключения к DeepSeek API"""
        print("\n🔍 Проверка DeepSeek API...")
        
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            print("  ❌ DEEPSEEK_API_KEY не установлен")
            self.results["checks"]["deepseek_api"] = "NO_KEY"
            return
        
        try:
            url = "https://api.deepseek.com/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 10
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, 
                                       timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 200:
                        print("  ✅ DeepSeek API доступен")
                        self.results["checks"]["deepseek_api"] = "OK"
                    elif response.status == 401:
                        print("  ❌ Неверный API ключ")
                        self.results["checks"]["deepseek_api"] = "INVALID_KEY"
                        self.results["errors"].append("DeepSeek API ключ недействителен")
                    else:
                        error_text = await response.text()
                        print(f"  ❌ HTTP {response.status}: {error_text[:100]}")
                        self.results["checks"]["deepseek_api"] = f"HTTP_{response.status}"
                        self.results["errors"].append(f"DeepSeek API HTTP {response.status}")
        except asyncio.TimeoutError:
            print("  ❌ Таймаут подключения")
            self.results["checks"]["deepseek_api"] = "TIMEOUT"
            self.results["errors"].append("DeepSeek API таймаут")
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")
            self.results["checks"]["deepseek_api"] = "ERROR"
            self.results["errors"].append(f"DeepSeek API: {e}")
    
    def check_python_packages(self):
        """Проверка установленных Python пакетов"""
        print("\n🔍 Проверка Python пакетов...")
        
        required_packages = {
            "telegram": "python-telegram-bot",
            "aiohttp": "aiohttp",
            "requests": "requests"
        }
        
        for module_name, package_name in required_packages.items():
            try:
                __import__(module_name)
                print(f"  ✅ {package_name}")
                self.results["checks"][f"package_{module_name}"] = "OK"
            except ImportError:
                print(f"  ❌ {package_name} НЕ УСТАНОВЛЕН")
                self.results["checks"][f"package_{module_name}"] = "MISSING"
                self.results["errors"].append(f"Пакет {package_name} не установлен")
    
    def check_permissions(self):
        """Проверка прав доступа"""
        print("\n🔍 Проверка прав доступа...")
        
        current_dir = Path(".")
        
        # Проверка прав на запись
        test_file = current_dir / ".write_test"
        try:
            test_file.write_text("test")
            test_file.unlink()
            print("  ✅ Права на запись в текущую директорию")
            self.results["checks"]["write_permission"] = "OK"
        except Exception as e:
            print(f"  ❌ Нет прав на запись: {e}")
            self.results["checks"]["write_permission"] = "DENIED"
            self.results["errors"].append("Нет прав на запись")
    
    def check_system_info(self):
        """Информация о системе"""
        print("\n🔍 Информация о системе...")
        
        print(f"  🐍 Python: {sys.version.split()[0]}")
        print(f"  💻 Платформа: {sys.platform}")
        print(f"  📂 Рабочая директория: {Path.cwd()}")
        
        self.results["checks"]["python_version"] = sys.version.split()[0]
        self.results["checks"]["platform"] = sys.platform
    
    def generate_report(self):
        """Генерация итогового отчета"""
        print("\n" + "="*60)
        print("📊 ИТОГОВЫЙ ОТЧЕТ ДИАГНОСТИКИ")
        print("="*60)
        
        total_checks = len(self.results["checks"])
        ok_checks = sum(1 for v in self.results["checks"].values() if v == "OK")
        
        print(f"\n✅ Успешных проверок: {ok_checks}/{total_checks}")
        
        if self.results["errors"]:
            print(f"\n❌ Ошибки ({len(self.results['errors'])}):")
            for error in self.results["errors"]:
                print(f"  • {error}")
        
        if self.results["warnings"]:
            print(f"\n⚠️  Предупреждения ({len(self.results['warnings'])}):")
            for warning in self.results["warnings"]:
                print(f"  • {warning}")
        
        # Сохранение отчета в JSON
        report_file = Path(f"diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        
        print(f"\n💾 Отчет сохранен: {report_file}")
        
        # Рекомендации
        print("\n💡 РЕКОМЕНДАЦИИ:")
        if not self.results["errors"]:
            print("  ✅ Все системы работают нормально!")
        else:
            if any("TOKEN" in e or "KEY" in e for e in self.results["errors"]):
                print("  • Проверьте переменные окружения (BOT_TOKEN, DEEPSEEK_API_KEY)")
            if any("пакет" in e.lower() for e in self.results["errors"]):
                print("  • Установите недостающие пакеты: pip install -r requirements.txt")
            if any("API" in e for e in self.results["errors"]):
                print("  • Проверьте сетевое подключение и API ключи")
        
        print("\n" + "="*60 + "\n")
        
        return len(self.results["errors"]) == 0
    
    async def run_all_checks(self):
        """Запуск всех проверок"""
        print("🚀 Запуск диагностики Family Assistant Bot\n")
        
        self.check_system_info()
        self.check_environment_variables()
        self.check_files()
        self.check_database()
        self.check_python_packages()
        self.check_permissions()
        await self.check_telegram_api()
        await self.check_deepseek_api()
        
        success = self.generate_report()
        return success

async def main():
    """Главная функция"""
    # Загружаем .env если есть
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    
    diagnostics = BotDiagnostics()
    success = await diagnostics.run_all_checks()
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
