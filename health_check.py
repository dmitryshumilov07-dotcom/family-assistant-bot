#!/usr/bin/env python3
"""
Lightweight health check script for monitoring
Returns exit code 0 if healthy, 1 if unhealthy
"""

import os
import sys
import json
from pathlib import Path

# Загружаем .env если есть
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def quick_health_check():
    """Quick health check for monitoring"""
    errors = []
    
    # 1. Проверка токенов
    if not os.getenv("BOT_TOKEN"):
        errors.append("BOT_TOKEN not set")
    
    if not os.getenv("DEEPSEEK_API_KEY"):
        errors.append("DEEPSEEK_API_KEY not set")
    
    # 2. Проверка файлов
    required_files = ["bot.py", "user_profiles.py", "deepseek_api.py"]
    for filename in required_files:
        if not Path(filename).exists():
            errors.append(f"Missing file: {filename}")
    
    # 3. Проверка базы данных
    db_path = Path("family_data.json")
    if db_path.exists():
        try:
            with open(db_path, 'r') as f:
                json.load(f)
        except json.JSONDecodeError:
            errors.append("Database corrupted")
    
    # Результат
    if errors:
        print("UNHEALTHY")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("HEALTHY")
        return True

if __name__ == "__main__":
    is_healthy = quick_health_check()
    sys.exit(0 if is_healthy else 1)
