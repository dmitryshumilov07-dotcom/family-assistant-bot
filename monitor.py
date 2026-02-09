#!/usr/bin/env python3
"""
Real-time monitoring script for Family Assistant Bot
Displays current status and statistics
"""

import os
import json
import sys
from pathlib import Path
from datetime import datetime

# Загружаем .env если есть
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def format_timestamp(iso_string):
    """Format ISO timestamp to readable format"""
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return iso_string

def display_status():
    """Display current bot status"""
    
    print("=" * 70)
    print("🤖 FAMILY ASSISTANT BOT - СТАТУС МОНИТОРИНГА")
    print("=" * 70)
    print(f"📅 Время проверки: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 1. Переменные окружения
    print("🔐 ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ:")
    bot_token = os.getenv("BOT_TOKEN")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    
    if bot_token:
        print(f"  ✅ BOT_TOKEN: {bot_token[:10]}... (установлен)")
    else:
        print(f"  ❌ BOT_TOKEN: не установлен")
    
    if deepseek_key:
        print(f"  ✅ DEEPSEEK_API_KEY: {deepseek_key[:10]}... (установлен)")
    else:
        print(f"  ❌ DEEPSEEK_API_KEY: не установлен")
    
    print()
    
    # 2. База данных
    print("💾 БАЗА ДАННЫХ:")
    db_path = Path("family_data.json")
    
    if db_path.exists():
        try:
            with open(db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            print(f"  ✅ Статус: найдена и валидна")
            print(f"  📏 Размер: {db_path.stat().st_size} байт")
            
            # Статистика пользователей
            users = data.get('users', {})
            print(f"  👥 Пользователей: {len(users)}")
            
            for user_id, user_data in users.items():
                name = user_data.get('name', 'Неизвестно')
                history_count = len(user_data.get('chat_history', []))
                print(f"     • {name} (ID: {user_id}): {history_count} сообщений")
            
            # Список покупок
            shopping_list = data.get('shopping_list', [])
            print(f"  🛒 Список покупок: {len(shopping_list)} позиций")
            
            if shopping_list:
                print("     Последние 3 позиции:")
                for item in shopping_list[-3:]:
                    print(f"       - {item}")
            
        except json.JSONDecodeError:
            print(f"  ❌ Статус: повреждена")
        except Exception as e:
            print(f"  ❌ Ошибка чтения: {e}")
    else:
        print(f"  ⚠️  Статус: не найдена")
    
    print()
    
    # 3. Файлы проекта
    print("📁 ФАЙЛЫ ПРОЕКТА:")
    files_to_check = {
        "bot.py": "Основной файл бота",
        "user_profiles.py": "Управление профилями",
        "deepseek_api.py": "API DeepSeek",
        "config.py": "Конфигурация",
        "requirements.txt": "Зависимости"
    }
    
    for filename, description in files_to_check.items():
        filepath = Path(filename)
        if filepath.exists():
            size = filepath.stat().st_size
            print(f"  ✅ {filename:20} {description:25} ({size} bytes)")
        else:
            print(f"  ❌ {filename:20} {description:25} (не найден)")
    
    print()
    
    # 4. Последний отчет диагностики
    print("📊 ПОСЛЕДНЯЯ ДИАГНОСТИКА:")
    
    reports = list(Path(".").glob("diagnostic_report_*.json"))
    if reports:
        latest_report = sorted(reports, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        
        with open(latest_report, 'r', encoding='utf-8') as f:
            report_data = json.load(f)
        
        print(f"  📄 Отчет: {latest_report.name}")
        print(f"  🕐 Время: {format_timestamp(report_data['timestamp'])}")
        
        checks = report_data.get('checks', {})
        ok_count = sum(1 for v in checks.values() if v == "OK")
        total_count = len(checks)
        
        print(f"  ✅ Проверок пройдено: {ok_count}/{total_count}")
        
        errors = report_data.get('errors', [])
        if errors:
            print(f"  ❌ Ошибок: {len(errors)}")
            for error in errors:
                print(f"     • {error}")
        
        warnings = report_data.get('warnings', [])
        if warnings:
            print(f"  ⚠️  Предупреждений: {len(warnings)}")
            for warning in warnings:
                print(f"     • {warning}")
        
        # Статус API
        telegram_status = checks.get('telegram_api', 'UNKNOWN')
        deepseek_status = checks.get('deepseek_api', 'UNKNOWN')
        
        print(f"\n  🔌 API СТАТУС:")
        print(f"     • Telegram: {telegram_status}")
        if telegram_status == "OK":
            bot_username = checks.get('bot_username', 'N/A')
            print(f"       (@{bot_username})")
        print(f"     • DeepSeek: {deepseek_status}")
        
    else:
        print(f"  ⚠️  Отчеты диагностики не найдены")
        print(f"  💡 Запустите: python3 diagnostics.py")
    
    print()
    print("=" * 70)
    
    # Общий статус
    print("\n🚦 ОБЩИЙ СТАТУС: ", end="")
    
    if db_path.exists() and bot_token and deepseek_key:
        if reports and report_data.get('errors'):
            print("⚠️  ЧАСТИЧНО РАБОТОСПОСОБЕН (есть ошибки)")
        else:
            print("✅ РАБОТОСПОСОБЕН")
    else:
        print("❌ ТРЕБУЕТСЯ НАСТРОЙКА")
    
    print()

if __name__ == "__main__":
    try:
        display_status()
    except KeyboardInterrupt:
        print("\n\n⚠️  Прервано пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка мониторинга: {e}")
        sys.exit(1)
