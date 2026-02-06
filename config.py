# Контекст ассистента
import os
from dataclasses import dataclass
from typing import List
ASSISTANT_CONTEXT = """
Ты - семейный ассистент для домашней группы. Твои задачи:
1. Помогать с планированием дел по дому
2. Напоминать о важных событиях
3. Предлагать решения бытовых вопросов
4. Помогать с составлением списков покупок
5. Отвечать на вопросы о домашних делах

Будь добрым, внимательным и полезным. Обращайся ко всем уважительно.
Используй неформальное общение, как с членами семьи.
"""


@dataclass(frozen=True)
class VKVideoSearchConfig:
    """Configuration for VK video search service."""

    access_tokens: List[str]
    api_version: str = "5.199"
    rate_limit_per_sec: float = 3.0
    cache_ttl_seconds: int = 3600
    cache_max_entries: int = 1000
    request_timeout_sec: int = 10
    auth_error_cooldown_sec: int = 900


def load_vk_config() -> VKVideoSearchConfig:
    """Load VK config from environment variables."""

    tokens_raw = os.getenv("VK_ACCESS_TOKENS") or os.getenv("VK_ACCESS_TOKEN") or ""
    access_tokens = [token.strip() for token in tokens_raw.split(",") if token.strip()]
    return VKVideoSearchConfig(access_tokens=access_tokens)