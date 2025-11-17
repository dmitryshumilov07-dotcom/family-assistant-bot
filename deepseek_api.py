import requests
import os
from config import ASSISTANT_CONTEXT

class DeepSeekAssistant:
    def __init__(self):
        self.api_key = os.getenv('DEEPSEEK_API_KEY')
        self.api_url = "https://api.deepseek.com/v1/chat/completions"
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}'
        }
    
    def ask_assistant(self, user_message, user_id=None):
        """Отправляем запрос к DeepSeek API"""
        
        messages = [
            {
                "role": "system",
                "content": ASSISTANT_CONTEXT
            },
            {
                "role": "user", 
                "content": user_message
            }
        ]
        
        payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        try:
            response = requests.post(
                self.api_url,
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                return result['choices'][0]['message']['content']
            else:
                return f"Ошибка API: {response.status_code} - {response.text}"
                
        except Exception as e:
            return f"Произошла ошибка: {str(e)}"

# Глобальный экземпляр ассистента
assistant = DeepSeekAssistant()