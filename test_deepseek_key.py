#!/usr/bin/env python3
"""
Test DeepSeek API key validity
Quick check to verify if the API key is working
"""

import os
import asyncio
import aiohttp
import sys

# Загружаем .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

async def test_deepseek_api():
    """Test DeepSeek API with a simple request"""
    
    api_key = os.getenv("DEEPSEEK_API_KEY")
    
    print("🔍 Проверка DeepSeek API ключа...\n")
    
    if not api_key:
        print("❌ DEEPSEEK_API_KEY не установлен")
        print("💡 Установите ключ в файле .env")
        return False
    
    print(f"🔑 API Key: {api_key[:10]}...{api_key[-4:]}")
    print()
    
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Минимальный тестовый запрос
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "привет"}],
        "max_tokens": 20
    }
    
    try:
        print("📡 Отправка тестового запроса...")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, 
                                   timeout=aiohttp.ClientTimeout(total=20)) as response:
                
                status = response.status
                print(f"📊 HTTP Status: {status}")
                
                if status == 200:
                    result = await response.json()
                    
                    if "choices" in result and len(result["choices"]) > 0:
                        ai_response = result["choices"][0]["message"]["content"]
                        
                        print("\n✅ API КЛЮЧ РАБОТАЕТ!")
                        print(f"\n🤖 Тестовый ответ AI:")
                        print(f"   \"{ai_response}\"")
                        
                        # Дополнительная информация
                        if "usage" in result:
                            usage = result["usage"]
                            print(f"\n📊 Использование токенов:")
                            print(f"   • Запрос: {usage.get('prompt_tokens', 0)}")
                            print(f"   • Ответ: {usage.get('completion_tokens', 0)}")
                            print(f"   • Всего: {usage.get('total_tokens', 0)}")
                        
                        if "model" in result:
                            print(f"\n🏷️  Модель: {result['model']}")
                        
                        return True
                    else:
                        print("\n❌ Неожиданный формат ответа")
                        print(result)
                        return False
                
                elif status == 401:
                    print("\n❌ НЕВЕРНЫЙ API КЛЮЧ")
                    print("💡 Проверьте ключ на https://platform.deepseek.com")
                    print("💡 Получите новый ключ если необходимо")
                    return False
                
                elif status == 429:
                    print("\n⚠️  ПРЕВЫШЕН ЛИМИТ ЗАПРОСОВ")
                    print("💡 Слишком много запросов к API")
                    print("💡 Подождите несколько минут и попробуйте снова")
                    return False
                
                elif status == 402:
                    print("\n💳 НЕДОСТАТОЧНО СРЕДСТВ")
                    print("💡 Пополните баланс на https://platform.deepseek.com")
                    return False
                
                else:
                    error_text = await response.text()
                    print(f"\n❌ ОШИБКА API")
                    print(f"Код: {status}")
                    print(f"Ответ: {error_text[:200]}")
                    return False
    
    except asyncio.TimeoutError:
        print("\n⏱️  ТАЙМАУТ")
        print("💡 Проверьте интернет-соединение")
        return False
    
    except aiohttp.ClientConnectorError:
        print("\n🌐 ОШИБКА ПОДКЛЮЧЕНИЯ")
        print("💡 Проверьте интернет-соединение")
        print("💡 Проверьте доступность api.deepseek.com")
        return False
    
    except Exception as e:
        print(f"\n❌ НЕОЖИДАННАЯ ОШИБКА")
        print(f"   {type(e).__name__}: {e}")
        return False

async def main():
    """Main function"""
    print("=" * 60)
    print("🧪 ТЕСТ API КЛЮЧА DEEPSEEK")
    print("=" * 60)
    print()
    
    success = await test_deepseek_api()
    
    print()
    print("=" * 60)
    
    if success:
        print("✅ РЕЗУЛЬТАТ: API ключ валиден и работает")
        sys.exit(0)
    else:
        print("❌ РЕЗУЛЬТАТ: API ключ не работает")
        print()
        print("📋 ЧТО ДЕЛАТЬ:")
        print("1. Проверьте правильность ключа в .env")
        print("2. Убедитесь что на аккаунте есть средства")
        print("3. Проверьте что ключ не истек")
        print("4. Получите новый ключ на https://platform.deepseek.com")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
