#!/usr/bin/env python3
"""
Production-ready voice recognition service for OpenClaw.

Features:
 - Robust ffmpeg/ffprobe error handling
 - Input audio validation
 - Cache with TTL and max entries
 - Multi-language support (ru, en) with auto detection
 - Vosk as local ASR with streaming support
 - Whisper API fallback (optional)
 - Async API and batch processing helpers
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import queue
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import config as app_config
except Exception:  # pragma: no cover - config is optional in tests
    app_config = None

try:
    from vosk import Model, KaldiRecognizer

    VOSK_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    Model = None
    KaldiRecognizer = None
    VOSK_AVAILABLE = False

import aiohttp

logger = logging.getLogger(__name__)


class VoiceServiceError(Exception):
    """Base error for voice service."""


class AudioValidationError(VoiceServiceError):
    """Audio validation failed."""


class AudioConversionError(VoiceServiceError):
    """Audio conversion failed."""


class ASRProviderError(VoiceServiceError):
    """ASR provider failed."""


class TranscriptionError(VoiceServiceError):
    """Transcription failed after retries/fallback."""


@dataclass(frozen=True)
class AudioInfo:
    duration_seconds: float
    sample_rate: int
    channels: int
    format_name: str
    size_bytes: int


@dataclass(frozen=True)
class TranscriptionSegment:
    text: str
    start: Optional[float] = None
    end: Optional[float] = None
    words: Optional[List[Dict[str, Any]]] = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: Optional[str]
    provider: str
    segments: Tuple[TranscriptionSegment, ...] = ()
    confidence: Optional[float] = None
    duration: Optional[float] = None


@dataclass
class RetryPolicy:
    attempts: int = 2
    backoff_seconds: float = 0.5


def _config_value(name: str, default: Any) -> Any:
    if app_config is None:
        return default
    return getattr(app_config, name, default)


@dataclass
class VoiceServiceConfig:
    model_paths: Dict[str, str] = field(
        default_factory=lambda: dict(
            _config_value(
                "VOICE_MODEL_PATHS",
                {
                    "ru": "/tmp/vosk-model/vosk-model-small-ru-0.22",
                    "en": "/tmp/vosk-model/vosk-model-small-en-us-0.15",
                },
            )
        )
    )
    allowed_languages: Tuple[str, ...] = field(
        default_factory=lambda: tuple(
            _config_value("VOICE_ALLOWED_LANGUAGES", ("ru", "en"))
        )
    )
    default_language: str = field(
        default_factory=lambda: _config_value("VOICE_DEFAULT_LANGUAGE", "ru")
    )
    cache_dir: str = field(
        default_factory=lambda: _config_value(
            "VOICE_CACHE_DIR", "/tmp/openclaw_voice_cache"
        )
    )
    cache_ttl_seconds: int = field(
        default_factory=lambda: int(_config_value("VOICE_CACHE_TTL_SECONDS", 86400))
    )
    max_cache_entries: int = field(
        default_factory=lambda: int(_config_value("VOICE_MAX_CACHE_ENTRIES", 1000))
    )
    max_audio_mb: int = field(
        default_factory=lambda: int(_config_value("VOICE_MAX_AUDIO_MB", 50))
    )
    max_audio_seconds: int = field(
        default_factory=lambda: int(_config_value("VOICE_MAX_AUDIO_SECONDS", 900))
    )
    ffmpeg_path: str = field(
        default_factory=lambda: _config_value("VOICE_FFMPEG_PATH", "ffmpeg")
    )
    ffprobe_path: str = field(
        default_factory=lambda: _config_value("VOICE_FFPROBE_PATH", "ffprobe")
    )
    whisper_api_url: str = field(
        default_factory=lambda: _config_value(
            "VOICE_WHISPER_API_URL",
            "https://api.openai.com/v1/audio/transcriptions",
        )
    )
    whisper_api_key: str = field(
        default_factory=lambda: _config_value("VOICE_WHISPER_API_KEY", "")
    )
    whisper_model: str = field(
        default_factory=lambda: _config_value("VOICE_WHISPER_MODEL", "whisper-1")
    )
    provider_priority: Tuple[str, ...] = field(
        default_factory=lambda: tuple(
            _config_value("VOICE_PROVIDER_PRIORITY", ("vosk", "whisper"))
        )
    )
    chunk_size_frames: int = 4000
    detect_sample_seconds: int = 10
    min_text_length: int = 1
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    allowed_formats: Tuple[str, ...] = (
        "ogg",
        "wav",
        "mp3",
        "m4a",
        "flac",
        "opus",
        "aac",
        "webm",
    )
    batch_concurrency: int = 2


class AudioValidator:
    """Validate audio files using ffprobe."""

    def __init__(
        self,
        ffprobe_path: str,
        max_audio_mb: int,
        max_audio_seconds: int,
        allowed_formats: Sequence[str],
    ) -> None:
        self.ffprobe_path = ffprobe_path
        self.max_audio_bytes = max_audio_mb * 1024 * 1024
        self.max_audio_seconds = max_audio_seconds
        self.allowed_formats = {fmt.lower() for fmt in allowed_formats}

    def validate(self, audio_path: str) -> AudioInfo:
        if not os.path.isfile(audio_path):
            raise AudioValidationError(f"Audio file not found: {audio_path}")

        size_bytes = os.path.getsize(audio_path)
        if size_bytes == 0:
            raise AudioValidationError("Audio file is empty")
        if size_bytes > self.max_audio_bytes:
            raise AudioValidationError(
                f"Audio file exceeds size limit: {size_bytes} bytes"
            )

        ext = os.path.splitext(audio_path)[1].lower().lstrip(".")
        if self.allowed_formats and ext and ext not in self.allowed_formats:
            raise AudioValidationError(f"Unsupported audio extension: .{ext}")

        probe = self._probe_audio(audio_path)
        if probe.duration_seconds <= 0:
            raise AudioValidationError("Audio duration is zero or invalid")
        if probe.duration_seconds > self.max_audio_seconds:
            raise AudioValidationError(
                f"Audio duration exceeds limit: {probe.duration_seconds}s"
            )

        return probe

    def _probe_audio(self, audio_path: str) -> AudioInfo:
        cmd = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name",
            "-show_entries",
            "stream=sample_rate,channels,codec_type",
            "-of",
            "json",
            audio_path,
        ]

        try:
            completed = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise AudioValidationError(
                "ffprobe not found. Please install ffmpeg."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or exc.stdout or "").strip()
            raise AudioValidationError(
                f"ffprobe failed: {stderr or exc}"
            ) from exc

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AudioValidationError("Invalid ffprobe output") from exc

        streams = payload.get("streams", [])
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        if not audio_streams:
            raise AudioValidationError("No audio stream detected")

        stream = audio_streams[0]
        sample_rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
        fmt = payload.get("format", {})
        duration = float(fmt.get("duration") or 0)
        format_name = fmt.get("format_name") or "unknown"

        if sample_rate <= 0 or channels <= 0:
            raise AudioValidationError("Invalid audio stream metadata")

        return AudioInfo(
            duration_seconds=duration,
            sample_rate=sample_rate,
            channels=channels,
            format_name=format_name,
            size_bytes=os.path.getsize(audio_path),
        )


class AudioConverter:
    """Convert input audio to a Vosk-friendly WAV format."""

    def __init__(self, ffmpeg_path: str) -> None:
        self.ffmpeg_path = ffmpeg_path

    def ensure_wav(self, input_path: str) -> Tuple[str, bool]:
        if input_path.lower().endswith(".wav"):
            with contextlib.suppress(wave.Error, EOFError):
                with wave.open(input_path, "rb") as wf:
                    if (
                        wf.getnchannels() == 1
                        and wf.getsampwidth() == 2
                        and wf.getframerate() == 16000
                    ):
                        return input_path, False
        return self.convert_to_wav(input_path), True

    def convert_to_wav(self, input_path: str) -> str:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_file.close()
        output_path = tmp_file.name

        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            input_path,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-acodec",
            "pcm_s16le",
            "-y",
            output_path,
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            return output_path
        except FileNotFoundError as exc:
            self._safe_remove(output_path)
            raise AudioConversionError(
                "ffmpeg not found. Please install ffmpeg."
            ) from exc
        except subprocess.CalledProcessError as exc:
            self._safe_remove(output_path)
            stderr = (exc.stderr or exc.stdout or "").strip()
            raise AudioConversionError(
                f"ffmpeg failed: {stderr or exc}"
            ) from exc

    @staticmethod
    def _safe_remove(path: str) -> None:
        with contextlib.suppress(OSError):
            os.remove(path)


class TranscriptionCache:
    """SQLite-based cache for transcription results."""

    def __init__(self, cache_dir: str, ttl_seconds: int, max_entries: int) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.Lock()
        os.makedirs(cache_dir, exist_ok=True)
        self._db_path = os.path.join(cache_dir, "transcription_cache.sqlite3")
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    cache_key TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    language TEXT,
                    provider TEXT,
                    confidence REAL,
                    duration REAL,
                    created_at REAL
                )
                """
            )
            conn.commit()

    @contextlib.contextmanager
    def _connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30)
        try:
            yield conn
        finally:
            conn.close()

    def make_key(self, audio_path: str, language: Optional[str]) -> str:
        hasher = hashlib.sha256()
        with open(audio_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                hasher.update(chunk)
        lang = language or "auto"
        return f"{lang}:{hasher.hexdigest()}"

    def get(self, cache_key: str) -> Optional[TranscriptionResult]:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT text, language, provider, confidence, duration, created_at
                FROM cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()

            if not row:
                return None

            text, language, provider, confidence, duration, created_at = row
            if self.ttl_seconds > 0 and (now - created_at) > self.ttl_seconds:
                conn.execute(
                    "DELETE FROM cache WHERE cache_key = ?",
                    (cache_key,),
                )
                conn.commit()
                return None

        return TranscriptionResult(
            text=text,
            language=language,
            provider=provider,
            confidence=confidence,
            duration=duration,
        )

    def set(self, cache_key: str, result: TranscriptionResult) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache
                (cache_key, text, language, provider, confidence, duration, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    result.text,
                    result.language,
                    result.provider,
                    result.confidence,
                    result.duration,
                    time.time(),
                ),
            )
            conn.commit()
            self._prune(conn)

    def _prune(self, conn: sqlite3.Connection) -> None:
        if self.max_entries <= 0:
            return
        count_row = conn.execute("SELECT COUNT(*) FROM cache").fetchone()
        if not count_row or count_row[0] <= self.max_entries:
            return
        overflow = count_row[0] - self.max_entries
        conn.execute(
            """
            DELETE FROM cache
            WHERE cache_key IN (
                SELECT cache_key FROM cache
                ORDER BY created_at ASC
                LIMIT ?
            )
            """,
            (overflow,),
        )
        conn.commit()


class ASRProvider(ABC):
    name: str
    supported_languages: Optional[Sequence[str]] = None

    def supports_language(self, language: Optional[str]) -> bool:
        if not language or not self.supported_languages:
            return True
        return language in self.supported_languages

    @abstractmethod
    async def transcribe(
        self, audio_path: str, language: Optional[str]
    ) -> TranscriptionResult:
        raise NotImplementedError

    async def stream_transcribe(
        self, audio_path: str, language: Optional[str]
    ) -> AsyncGenerator[TranscriptionSegment, None]:
        raise NotImplementedError


class VoskModelFactory:
    def __init__(self, model_paths: Dict[str, str]) -> None:
        self.model_paths = model_paths
        self._models: Dict[str, Model] = {}
        self._lock = threading.Lock()

    def get(self, language: str) -> Model:
        if not VOSK_AVAILABLE:
            raise ASRProviderError("vosk is not installed")

        path = self.model_paths.get(language)
        if not path:
            raise ASRProviderError(f"No Vosk model configured for {language}")
        if not os.path.isdir(path):
            raise ASRProviderError(f"Vosk model not found at {path}")

        with self._lock:
            if language not in self._models:
                logger.info("Loading Vosk model: %s", path)
                self._models[language] = Model(path)
            return self._models[language]


class VoskProvider(ASRProvider):
    name = "vosk"

    def __init__(
        self,
        language: str,
        model_factory: VoskModelFactory,
        chunk_size_frames: int,
    ) -> None:
        self.language = language
        self.supported_languages = (language,)
        self.model_factory = model_factory
        self.chunk_size_frames = chunk_size_frames

    async def transcribe(
        self, audio_path: str, language: Optional[str] = None
    ) -> TranscriptionResult:
        return await asyncio.to_thread(self._transcribe_sync, audio_path)

    async def stream_transcribe(
        self, audio_path: str, language: Optional[str] = None
    ) -> AsyncGenerator[TranscriptionSegment, None]:
        loop = asyncio.get_running_loop()
        output_queue: "queue.Queue[Any]" = queue.Queue()

        def worker() -> None:
            try:
                for segment in self._stream_transcribe_sync(audio_path):
                    loop.call_soon_threadsafe(output_queue.put, segment)
            except Exception as exc:  # pragma: no cover - thread errors
                loop.call_soon_threadsafe(output_queue.put, exc)
            finally:
                loop.call_soon_threadsafe(output_queue.put, None)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await asyncio.to_thread(output_queue.get)
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    def _transcribe_sync(self, audio_path: str) -> TranscriptionResult:
        segments = list(self._stream_transcribe_sync(audio_path))
        text = " ".join(seg.text for seg in segments if seg.text).strip()
        confidence = self._compute_confidence(segments)
        duration = self._estimate_duration(segments)
        return TranscriptionResult(
            text=text,
            language=self.language,
            provider=self.name,
            segments=tuple(segments),
            confidence=confidence,
            duration=duration,
        )

    def _stream_transcribe_sync(
        self, audio_path: str
    ) -> Iterable[TranscriptionSegment]:
        model = self.model_factory.get(self.language)
        with wave.open(audio_path, "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise ASRProviderError("WAV must be mono 16-bit PCM")

            recognizer = KaldiRecognizer(model, wf.getframerate())
            recognizer.SetWords(True)

            while True:
                data = wf.readframes(self.chunk_size_frames)
                if not data:
                    break
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    segment = self._segment_from_result(result)
                    if segment.text:
                        yield segment

            final = json.loads(recognizer.FinalResult())
            final_segment = self._segment_from_result(final)
            if final_segment.text:
                yield final_segment

    @staticmethod
    def _segment_from_result(payload: Dict[str, Any]) -> TranscriptionSegment:
        text = payload.get("text", "") or ""
        words = payload.get("result") or []
        start = None
        end = None
        if words:
            start = words[0].get("start")
            end = words[-1].get("end")
        return TranscriptionSegment(
            text=text,
            start=start,
            end=end,
            words=words if words else None,
        )

    @staticmethod
    def _compute_confidence(segments: Iterable[TranscriptionSegment]) -> Optional[float]:
        scores = []
        for segment in segments:
            if not segment.words:
                continue
            for word in segment.words:
                if "conf" in word:
                    scores.append(word["conf"])
        if not scores:
            return None
        return sum(scores) / len(scores)

    @staticmethod
    def _estimate_duration(segments: Iterable[TranscriptionSegment]) -> Optional[float]:
        end_times = [seg.end for seg in segments if seg.end is not None]
        if not end_times:
            return None
        return max(end_times)


class WhisperAPIProvider(ASRProvider):
    name = "whisper"
    supported_languages = None

    def __init__(self, api_url: str, api_key: str, model: str) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.model = model

    async def transcribe(
        self, audio_path: str, language: Optional[str]
    ) -> TranscriptionResult:
        if not self.api_key:
            raise ASRProviderError("Whisper API key is not configured")

        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = aiohttp.FormData()
        data.add_field("model", self.model)
        if language:
            data.add_field("language", language)

        with open(audio_path, "rb") as fh:
            data.add_field("file", fh, filename=os.path.basename(audio_path))
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url, data=data, headers=headers
                ) as response:
                    body = await response.text()
                    if response.status != 200:
                        raise ASRProviderError(
                            f"Whisper API failed ({response.status}): {body}"
                        )
                    payload = json.loads(body)

        text = payload.get("text", "") or ""
        return TranscriptionResult(
            text=text,
            language=language,
            provider=self.name,
            confidence=None,
            duration=None,
        )


class LanguageDetector:
    """Lightweight language detector based on short Vosk transcriptions."""

    _cyrillic_re = re.compile(r"[А-Яа-яЁё]")
    _latin_re = re.compile(r"[A-Za-z]")

    def __init__(
        self,
        model_factory: VoskModelFactory,
        chunk_size_frames: int,
        sample_seconds: int,
    ) -> None:
        self.model_factory = model_factory
        self.chunk_size_frames = chunk_size_frames
        self.sample_seconds = sample_seconds

    def detect(self, wav_path: str, languages: Sequence[str]) -> Optional[str]:
        scores: Dict[str, float] = {}
        for language in languages:
            try:
                text = self._quick_transcribe(wav_path, language)
            except VoiceServiceError:
                continue
            scores[language] = self._score_text(text, language)
        if not scores:
            return None
        return max(scores, key=scores.get)

    def _quick_transcribe(self, wav_path: str, language: str) -> str:
        model = self.model_factory.get(language)
        with wave.open(wav_path, "rb") as wf:
            recognizer = KaldiRecognizer(model, wf.getframerate())
            recognizer.SetWords(False)
            max_frames = int(wf.getframerate() * self.sample_seconds)
            read_frames = 0
            frame_size = wf.getsampwidth() * wf.getnchannels()
            while read_frames < max_frames:
                data = wf.readframes(self.chunk_size_frames)
                if not data:
                    break
                read_frames += len(data) // frame_size
                if recognizer.AcceptWaveform(data):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "")
                    if text:
                        return text
            final = json.loads(recognizer.FinalResult())
            return final.get("text", "") or ""

    def _score_text(self, text: str, language: str) -> float:
        if not text:
            return 0.0
        if language == "ru":
            return len(self._cyrillic_re.findall(text))
        if language == "en":
            return len(self._latin_re.findall(text))
        return len(text)


class VoiceRecognizer:
    """High-level voice recognition service."""

    def __init__(
        self,
        config: Optional[VoiceServiceConfig] = None,
        validator: Optional[AudioValidator] = None,
        converter: Optional[AudioConverter] = None,
        cache: Optional[TranscriptionCache] = None,
        providers: Optional[List[ASRProvider]] = None,
    ) -> None:
        self.config = config or VoiceServiceConfig()
        self.validator = validator or AudioValidator(
            ffprobe_path=self.config.ffprobe_path,
            max_audio_mb=self.config.max_audio_mb,
            max_audio_seconds=self.config.max_audio_seconds,
            allowed_formats=self.config.allowed_formats,
        )
        self.converter = converter or AudioConverter(self.config.ffmpeg_path)
        self.cache = cache or TranscriptionCache(
            cache_dir=self.config.cache_dir,
            ttl_seconds=self.config.cache_ttl_seconds,
            max_entries=self.config.max_cache_entries,
        )
        self.model_factory = VoskModelFactory(self.config.model_paths)
        self.language_detector = LanguageDetector(
            model_factory=self.model_factory,
            chunk_size_frames=self.config.chunk_size_frames,
            sample_seconds=self.config.detect_sample_seconds,
        )
        self.providers = providers or self._build_providers()

    def _build_providers(self) -> List[ASRProvider]:
        providers: List[ASRProvider] = []
        for provider_name in self.config.provider_priority:
            if provider_name == "vosk":
                if not VOSK_AVAILABLE:
                    logger.warning("Vosk is not available, skipping provider")
                    continue
                for language in self.config.allowed_languages:
                    if language in self.config.model_paths:
                        providers.append(
                            VoskProvider(
                                language=language,
                                model_factory=self.model_factory,
                                chunk_size_frames=self.config.chunk_size_frames,
                            )
                        )
            elif provider_name == "whisper":
                if self.config.whisper_api_key:
                    providers.append(
                        WhisperAPIProvider(
                            api_url=self.config.whisper_api_url,
                            api_key=self.config.whisper_api_key,
                            model=self.config.whisper_model,
                        )
                    )
        return providers

    async def transcribe(
        self,
        audio_path: str,
        language: str = "auto",
        use_cache: bool = True,
    ) -> TranscriptionResult:
        audio_path = os.path.abspath(audio_path)

        await asyncio.to_thread(self.validator.validate, audio_path)
        wav_path, cleanup = await asyncio.to_thread(
            self.converter.ensure_wav, audio_path
        )

        try:
            resolved_language = await self._resolve_language(wav_path, language)
            cache_key = None
            if use_cache:
                cache_key = self.cache.make_key(audio_path, resolved_language)
                cached = self.cache.get(cache_key)
                if cached:
                    return cached

            result = await self._transcribe_with_fallback(
                wav_path, resolved_language
            )

            if use_cache and cache_key:
                self.cache.set(cache_key, result)
            return result
        finally:
            if cleanup:
                AudioConverter._safe_remove(wav_path)

    async def stream_transcribe(
        self,
        audio_path: str,
        language: str = "auto",
    ) -> AsyncGenerator[TranscriptionSegment, None]:
        audio_path = os.path.abspath(audio_path)
        await asyncio.to_thread(self.validator.validate, audio_path)
        wav_path, cleanup = await asyncio.to_thread(
            self.converter.ensure_wav, audio_path
        )

        try:
            resolved_language = await self._resolve_language(wav_path, language)
            provider = self._select_streaming_provider(resolved_language)
            async for segment in provider.stream_transcribe(
                wav_path, resolved_language
            ):
                yield segment
        finally:
            if cleanup:
                AudioConverter._safe_remove(wav_path)

    async def batch_transcribe(
        self,
        audio_paths: Sequence[str],
        language: str = "auto",
        use_cache: bool = True,
    ) -> Dict[str, TranscriptionResult]:
        semaphore = asyncio.Semaphore(self.config.batch_concurrency)

        async def worker(path: str) -> Tuple[str, TranscriptionResult]:
            async with semaphore:
                result = await self.transcribe(
                    path, language=language, use_cache=use_cache
                )
                return path, result

        tasks = [worker(path) for path in audio_paths]
        results = await asyncio.gather(*tasks)
        return dict(results)

    async def _resolve_language(self, wav_path: str, language: str) -> Optional[str]:
        if language and language != "auto":
            return language
        if not self.config.allowed_languages:
            return self.config.default_language
        detected = await asyncio.to_thread(
            self.language_detector.detect, wav_path, self.config.allowed_languages
        )
        return detected or self.config.default_language

    async def _transcribe_with_fallback(
        self, wav_path: str, language: Optional[str]
    ) -> TranscriptionResult:
        providers = self._providers_for_language(language)
        if not providers:
            raise TranscriptionError("No ASR providers are configured")

        last_error: Optional[Exception] = None
        for provider in providers:
            for attempt in range(self.config.retry_policy.attempts):
                try:
                    result = await provider.transcribe(wav_path, language)
                    if len(result.text) >= self.config.min_text_length:
                        return result
                    raise ASRProviderError("Empty transcription result")
                except ASRProviderError as exc:
                    last_error = exc
                    await self._backoff(attempt)
                except Exception as exc:  # pragma: no cover - unexpected
                    last_error = exc
                    await self._backoff(attempt)
            logger.warning(
                "Provider %s failed, trying next provider", provider.name
            )

        raise TranscriptionError(
            f"Transcription failed after fallback: {last_error}"
        )

    def _providers_for_language(
        self, language: Optional[str]
    ) -> List[ASRProvider]:
        return [p for p in self.providers if p.supports_language(language)]

    def _select_streaming_provider(self, language: Optional[str]) -> ASRProvider:
        for provider in self._providers_for_language(language):
            if isinstance(provider, VoskProvider):
                return provider
        raise TranscriptionError("No streaming-capable provider available")

    async def _backoff(self, attempt: int) -> None:
        if self.config.retry_policy.attempts <= 1:
            return
        delay = self.config.retry_policy.backoff_seconds * (2**attempt)
        await asyncio.sleep(delay)


async def _demo_transcribe(path: str) -> None:
    recognizer = VoiceRecognizer()
    result = await recognizer.transcribe(path)
    print(result.text)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="OpenClaw voice service demo")
    parser.add_argument("audio_file", help="Path to audio file")
    parser.add_argument("--language", default="auto")
    args = parser.parse_args()

    asyncio.run(_demo_transcribe(args.audio_file))


if __name__ == "__main__":
    main()
