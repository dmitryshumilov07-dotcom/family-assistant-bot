import json
from datetime import datetime

class UserManager:
    def __init__(self, db_path='family_data.json'):
        self.db_path = db_path
    
    def _load_data(self):
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {"shopping_list": [], "users": {}}
    
    def _save_data(self, data):
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def get_user_profile(self, user_id):
        """Получить профиль пользователя по ID"""
        data = self._load_data()
        return data["users"].get(str(user_id))
    
    def is_user_allowed(self, user_id):
        """Проверка разрешен ли пользователь"""
        allowed_ids = [
            259917981,  # Замените на ID Ирины в Telegram
            160217558   # Замените на ID Дмитрия в Telegram
        ]
        return user_id in allowed_ids