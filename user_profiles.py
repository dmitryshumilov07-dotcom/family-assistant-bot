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
            123456,  # Замените на ID Ирины
            654321   # Замените на ID Дмитрия
        ]
        return user_id in allowed_ids

    def add_to_chat_history(self, user_id, role, message):
        """Добавить сообщение в историю диалога"""
        data = self._load_data()
        user_id_str = str(user_id)
        
        if user_id_str not in data["users"]:
            return
        
        if "chat_history" not in data["users"][user_id_str]:
            data["users"][user_id_str]["chat_history"] = []
        
        # Добавляем новое сообщение
        data["users"][user_id_str]["chat_history"].append({
            "role": role,  # "user" или "assistant"
            "content": message,
            "timestamp": datetime.now().isoformat()
        })
        
        # Ограничиваем историю последними 10 сообщениями
        if len(data["users"][user_id_str]["chat_history"]) > 10:
            data["users"][user_id_str]["chat_history"] = data["users"][user_id_str]["chat_history"][-10:]
        
        self._save_data(data)

    def get_chat_history(self, user_id):
        """Получить историю диалога для контекста"""
        data = self._load_data()
        user_id_str = str(user_id)
        
        if user_id_str not in data["users"] or "chat_history" not in data["users"][user_id_str]:
            return []
        
        return data["users"][user_id_str]["chat_history"]

    def clear_chat_history(self, user_id):
        """Очистить историю диалога"""
        data = self._load_data()
        user_id_str = str(user_id)
        
        if user_id_str in data["users"]:
            data["users"][user_id_str]["chat_history"] = []
            self._save_data(data)
            return True
        return False