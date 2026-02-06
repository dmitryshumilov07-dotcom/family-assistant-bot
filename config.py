import os

# Assistant context
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

# Voice service configuration
VOICE_MODEL_PATHS = {
    "ru": os.getenv("VOSK_MODEL_RU", "/tmp/vosk-model/vosk-model-small-ru-0.22"),
    "en": os.getenv("VOSK_MODEL_EN", "/tmp/vosk-model/vosk-model-small-en-us-0.15"),
}
VOICE_ALLOWED_LANGUAGES = tuple(
    lang.strip()
    for lang in os.getenv("VOICE_ALLOWED_LANGUAGES", "ru,en").split(",")
    if lang.strip()
)
VOICE_DEFAULT_LANGUAGE = os.getenv("VOICE_DEFAULT_LANGUAGE", "ru")
VOICE_CACHE_DIR = os.getenv("VOICE_CACHE_DIR", "/tmp/openclaw_voice_cache")
VOICE_CACHE_TTL_SECONDS = int(os.getenv("VOICE_CACHE_TTL_SECONDS", "86400"))
VOICE_MAX_CACHE_ENTRIES = int(os.getenv("VOICE_MAX_CACHE_ENTRIES", "1000"))
VOICE_MAX_AUDIO_MB = int(os.getenv("VOICE_MAX_AUDIO_MB", "50"))
VOICE_MAX_AUDIO_SECONDS = int(os.getenv("VOICE_MAX_AUDIO_SECONDS", "900"))
VOICE_FFMPEG_PATH = os.getenv("VOICE_FFMPEG_PATH", "ffmpeg")
VOICE_FFPROBE_PATH = os.getenv("VOICE_FFPROBE_PATH", "ffprobe")
VOICE_WHISPER_API_URL = os.getenv(
    "VOICE_WHISPER_API_URL",
    "https://api.openai.com/v1/audio/transcriptions",
)
VOICE_WHISPER_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    os.getenv("VOICE_WHISPER_API_KEY", ""),
)
VOICE_WHISPER_MODEL = os.getenv("VOICE_WHISPER_MODEL", "whisper-1")
VOICE_PROVIDER_PRIORITY = tuple(
    item.strip()
    for item in os.getenv("VOICE_PROVIDER_PRIORITY", "vosk,whisper").split(",")
    if item.strip()
)