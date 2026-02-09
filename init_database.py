#!/usr/bin/env python3
"""
Database initialization script for Family Assistant Bot
Creates the initial database with user profiles
"""

import json
from pathlib import Path
from datetime import datetime

def init_database():
    """Initialize the database with default structure"""
    
    db_path = Path("family_data.json")
    
    if db_path.exists():
        print("⚠️  База данных уже существует.")
        response = input("Перезаписать? (yes/no): ")
        if response.lower() != 'yes':
            print("❌ Отменено")
            return False
    
    # Структура базы данных
    database = {
        "shopping_list": [],
        "users": {
            "259917981": {  # Ирина
                "name": "Ирина",
                "role": "домохозяйка, мать, инженер",
                "chat_history": [],
                "created_at": datetime.now().isoformat()
            },
            "160217558": {  # Дмитрий
                "name": "Дмитрий",
                "role": "создатель",
                "chat_history": [],
                "created_at": datetime.now().isoformat()
            }
        },
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "version": "1.0"
        }
    }
    
    # Сохраняем базу данных
    with open(db_path, 'w', encoding='utf-8') as f:
        json.dump(database, f, ensure_ascii=False, indent=2)
    
    print(f"✅ База данных создана: {db_path}")
    print(f"📊 Пользователей: {len(database['users'])}")
    
    # Отображаем структуру
    print("\n📋 Структура базы данных:")
    print(json.dumps(database, ensure_ascii=False, indent=2))
    
    return True

if __name__ == "__main__":
    print("🔧 Инициализация базы данных Family Assistant Bot\n")
    success = init_database()
    
    if success:
        print("\n✅ Инициализация завершена успешно!")
    else:
        print("\n❌ Инициализация отменена")
