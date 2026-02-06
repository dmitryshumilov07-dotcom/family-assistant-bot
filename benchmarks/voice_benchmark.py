#!/usr/bin/env python3
"""
Simple performance benchmark for the voice recognition service.

Example:
  python benchmarks/voice_benchmark.py --audio ./sample.wav --language ru
"""

import argparse
import asyncio
import time

from voice_service_improved import VoiceRecognizer


async def run_benchmark(audio_path: str, language: str) -> None:
    recognizer = VoiceRecognizer()
    start = time.perf_counter()
    result = await recognizer.transcribe(audio_path, language=language)
    elapsed = time.perf_counter() - start
    print(f"Text length: {len(result.text)}")
    print(f"Provider: {result.provider}")
    print(f"Elapsed: {elapsed:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice service benchmark")
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--language", default="auto", help="Language or auto")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.audio, args.language))


if __name__ == "__main__":
    main()
