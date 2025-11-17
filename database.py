import json
import os
from datetime import datetime

class FamilyDatabase:
    def __init__(self, filename='family_data.json'):
        self.filename = filename
        self._ensure_file_exists()
    
    def _ensure_file_exists(self):
        if not os.path.exists(self.filename):
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump({
                    'reminders': [],
                    'shopping_list': [],
                    'tasks': [],
                    'users': []
                }, f, ensure_ascii=False, indent=2)
    
    def _read_data(self):
        with open(self.filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _write_data(self, data):
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def add_reminder(self, user_id, text, date):
        data = self._read_data()
        reminder = {
            'id': len(data['reminders']) + 1,
            'user_id': user_id,
            'text': text,
            'date': date,
            'created_at': datetime.now().isoformat()
        }
        data['reminders'].append(reminder)
        self._write_data(data)
        return reminder
    
    def get_user_reminders(self, user_id):
        data = self._read_data()
        return [r for r in data['reminders'] if r['user_id'] == user_id]
    
    def add_to_shopping_list(self, item, user_id):
        data = self._read_data()
        shopping_item = {
            'id': len(data['shopping_list']) + 1,
            'item': item,
            'added_by': user_id,
            'completed': False,
            'created_at': datetime.now().isoformat()
        }
        data['shopping_list'].append(shopping_item)
        self._write_data(data)
        return shopping_item
    
    def get_shopping_list(self):
        data = self._read_data()
        return data['shopping_list']

# Глобальный экземпляр базы данных
db = FamilyDatabase()