import os
import tempfile
import unittest
from unittest import mock

from voice_service_improved import (
    AudioConversionError,
    AudioConverter,
    AudioInfo,
    TranscriptionCache,
    TranscriptionResult,
    VoiceServiceConfig,
    VoiceRecognizer,
)


class DummyValidator:
    def validate(self, audio_path: str) -> AudioInfo:
        return AudioInfo(
            duration_seconds=1.0,
            sample_rate=16000,
            channels=1,
            format_name="wav",
            size_bytes=os.path.getsize(audio_path),
        )


class DummyConverter:
    def ensure_wav(self, audio_path: str):
        return audio_path, False


class DummyProvider:
    name = "dummy"
    supported_languages = ("ru", "en")

    def __init__(self, text: str = "hello") -> None:
        self.text = text
        self.calls = 0

    def supports_language(self, language: str) -> bool:
        if not language:
            return True
        return language in self.supported_languages

    async def transcribe(self, audio_path: str, language: str):
        self.calls += 1
        return TranscriptionResult(
            text=self.text,
            language=language,
            provider=self.name,
            confidence=0.9,
            duration=1.0,
        )


class CacheTests(unittest.TestCase):
    def test_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "sample.wav")
            with open(audio_path, "wb") as fh:
                fh.write(b"data")
            cache = TranscriptionCache(
                cache_dir=tmpdir,
                ttl_seconds=60,
                max_entries=10,
            )
            key = cache.make_key(audio_path, "ru")
            result = TranscriptionResult(
                text="test",
                language="ru",
                provider="dummy",
                confidence=0.5,
                duration=1.0,
            )
            cache.set(key, result)
            cached = cache.get(key)
            self.assertIsNotNone(cached)
            self.assertEqual(cached.text, "test")

    def test_cache_expiration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "sample.wav")
            with open(audio_path, "wb") as fh:
                fh.write(b"data")
            cache = TranscriptionCache(
                cache_dir=tmpdir,
                ttl_seconds=0,
                max_entries=10,
            )
            key = cache.make_key(audio_path, "ru")
            cache.set(
                key,
                TranscriptionResult(
                    text="expired",
                    language="ru",
                    provider="dummy",
                    confidence=0.5,
                    duration=1.0,
                ),
            )
            cached = cache.get(key)
            self.assertIsNone(cached)


class ConverterTests(unittest.TestCase):
    def test_converter_error_when_ffmpeg_missing(self):
        converter = AudioConverter("ffmpeg")
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(AudioConversionError):
                converter.convert_to_wav("input.ogg")


class VoiceRecognizerTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_uses_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "sample.wav")
            with open(audio_path, "wb") as fh:
                fh.write(b"data")

            provider = DummyProvider(text="cached")
            cache = TranscriptionCache(
                cache_dir=tmpdir,
                ttl_seconds=60,
                max_entries=10,
            )
            recognizer = VoiceRecognizer(
                validator=DummyValidator(),
                converter=DummyConverter(),
                cache=cache,
                providers=[provider],
            )

            result1 = await recognizer.transcribe(audio_path, language="ru")
            result2 = await recognizer.transcribe(audio_path, language="ru")
            self.assertEqual(result1.text, "cached")
            self.assertEqual(result2.text, "cached")
            self.assertEqual(provider.calls, 1)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(
        os.getenv("VOICE_TEST_AUDIO_FILE") and os.getenv("VOICE_TEST_MODEL_PATH"),
        "Integration test requires VOICE_TEST_AUDIO_FILE and VOICE_TEST_MODEL_PATH",
    )
    async def test_vosk_integration(self):
        audio_path = os.getenv("VOICE_TEST_AUDIO_FILE")
        model_path = os.getenv("VOICE_TEST_MODEL_PATH")
        config = VoiceServiceConfig(
            model_paths={"ru": model_path},
            allowed_languages=("ru",),
        )
        recognizer = VoiceRecognizer(config=config)
        result = await recognizer.transcribe(audio_path, language="ru")
        self.assertTrue(result.text)


if __name__ == "__main__":
    unittest.main()
