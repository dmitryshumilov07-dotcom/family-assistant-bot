#!/usr/bin/env python3
"""
Automatic fix script for common Family Assistant Bot issues
Attempts to automatically resolve known problems
"""

import os
import sys
import json
from pathlib import Path
import subprocess

def check_and_fix_database():
    """Check and fix database issues"""
    print("🔍 Проверка базы данных...")
    
    db_path = Path("family_data.json")
    
    if not db_path.exists():
        print("  ❌ База данных не найдена")
        print("  🔧 Создаю новую базу данных...")
        
        result = subprocess.run([sys.executable, "init_database.py"], 
                              input=b"yes\n",
                              capture_output=True)
        
        if result.returncode == 0:
            print("  ✅ База данных создана")
            return True
        else:
            print("  ❌ Ошибка создания БД")
            return False
    
    # Проверка валидности
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print("  ✅ База данных валидна")
        
        # Проверка структуры
        if "users" not in data:
            print("  ⚠️  Отсутствует секция users")
            data["users"] = {}
        
        if "shopping_list" not in data:
            print("  ⚠️  Отсутствует список покупок")
            data["shopping_list"] = []
        
        # Проверка истории чатов
        for user_id, user_data in data.get("users", {}).items():
            if "chat_history" not in user_data:
                print(f"  🔧 Добавляю chat_history для пользователя {user_id}")
                user_data["chat_history"] = []
        
        # Сохраняем исправленную версию
        with open(db_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return True
        
    except json.JSONDecodeError:
        print("  ❌ База данных повреждена")
        print("  🔧 Создаю резервную копию...")
        
        backup_path = db_path.with_suffix('.json.backup')
        db_path.rename(backup_path)
        print(f"  💾 Резервная копия: {backup_path}")
        
        print("  🔧 Создаю новую базу данных...")
        result = subprocess.run([sys.executable, "init_database.py"], 
                              input=b"yes\n",
                              capture_output=True)
        
        if result.returncode == 0:
            print("  ✅ База данных пересоздана")
            return True
        else:
            print("  ❌ Ошибка пересоздания БД")
            return False

def check_and_fix_dependencies():
    """Check and install missing dependencies"""
    print("\n🔍 Проверка зависимостей...")
    
    required_packages = {
        "telegram": "python-telegram-bot",
        "aiohttp": "aiohttp",
        "requests": "requests",
        "dotenv": "python-dotenv"
    }
    
    missing = []
    
    for module_name, package_name in required_packages.items():
        try:
            __import__(module_name)
            print(f"  ✅ {package_name}")
        except ImportError:
            print(f"  ❌ {package_name} не установлен")
            missing.append(package_name)
    
    if missing:
        print(f"\n  🔧 Устанавливаю недостающие пакеты...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print("  ✅ Зависимости установлены")
            return True
        else:
            print("  ❌ Ошибка установки зависимостей")
            print(result.stderr)
            return False
    
    return True

def check_environment_variables():
    """Check environment variables"""
    print("\n🔍 Проверка переменных окружения...")
    
    # Загружаем .env
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    
    issues = []
    
    bot_token = os.getenv("BOT_TOKEN")
    if bot_token:
        print(f"  ✅ BOT_TOKEN установлен")
    else:
        print(f"  ❌ BOT_TOKEN не установлен")
        issues.append("BOT_TOKEN")
    
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key:
        print(f"  ✅ DEEPSEEK_API_KEY установлен")
    else:
        print(f"  ❌ DEEPSEEK_API_KEY не установлен")
        issues.append("DEEPSEEK_API_KEY")
    
    if issues:
        print("\n  ⚠️  Требуется ручная настройка переменных окружения")
        print("  💡 Отредактируйте файл .env:")
        for var in issues:
            print(f"     {var}=ваше_значение")
        return False
    
    return True

def check_file_permissions():
    """Check file permissions"""
    print("\n🔍 Проверка прав доступа...")
    
    test_file = Path(".write_test")
    try:
        test_file.write_text("test")
        test_file.unlink()
        print("  ✅ Права на запись в рабочую директорию")
        return True
    except Exception as e:
        print(f"  ❌ Нет прав на запись: {e}")
        print("  💡 Проверьте права доступа к директории")
        return False

def create_gitignore():
    """Create or update .gitignore"""
    print("\n🔍 Проверка .gitignore...")
    
    gitignore_path = Path(".gitignore")
    
    required_entries = [
        ".env",
        "family_data.json",
        "family_data.json.backup",
        "diagnostic_report_*.json",
        "__pycache__/",
        "*.pyc",
        "*.pyo",
        "*.log"
    ]
    
    existing_entries = set()
    if gitignore_path.exists():
        existing_entries = set(gitignore_path.read_text().strip().split('\n'))
    
    new_entries = [e for e in required_entries if e not in existing_entries]
    
    if new_entries:
        print(f"  🔧 Добавляю {len(new_entries)} записей в .gitignore")
        
        with open(gitignore_path, 'a', encoding='utf-8') as f:
            if existing_entries and not gitignore_path.read_text().endswith('\n'):
                f.write('\n')
            
            f.write('\n# Family Assistant Bot - автоматически добавлено\n')
            for entry in new_entries:
                f.write(f'{entry}\n')
        
        print("  ✅ .gitignore обновлен")
    else:
        print("  ✅ .gitignore актуален")
    
    return True

def main():
    """Main function"""
    print("=" * 70)
    print("🔧 АВТОМАТИЧЕСКОЕ ИСПРАВЛЕНИЕ ПРОБЛЕМ")
    print("=" * 70)
    print()
    
    results = []
    
    # Проверки и исправления
    results.append(("База данных", check_and_fix_database()))
    results.append(("Зависимости", check_and_fix_dependencies()))
    results.append(("Переменные окружения", check_environment_variables()))
    results.append(("Права доступа", check_file_permissions()))
    results.append((".gitignore", create_gitignore()))
    
    # Итоги
    print("\n" + "=" * 70)
    print("📊 ИТОГИ ИСПРАВЛЕНИЯ")
    print("=" * 70)
    print()
    
    for name, success in results:
        status = "✅" if success else "❌"
        print(f"  {status} {name}")
    
    successful = sum(1 for _, success in results if success)
    total = len(results)
    
    print()
    print(f"Успешно: {successful}/{total}")
    
    if successful == total:
        print("\n✅ Все проблемы исправлены!")
        print("\n💡 Рекомендуется:")
        print("   1. Запустить диагностику: python3 diagnostics.py")
        print("   2. Проверить статус: python3 monitor.py")
        return 0
    else:
        print("\n⚠️  Некоторые проблемы требуют ручного исправления")
        print("\n💡 Смотрите инструкции выше")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Прервано пользователем")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
