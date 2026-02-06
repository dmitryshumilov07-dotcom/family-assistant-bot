import json
import os
import tempfile
import unittest

from weather_service_improved import (
    FileFallbackStore,
    MemoryCache,
    RateLimiter,
    WeatherApiError,
    WeatherResult,
    WeatherService,
    WeatherServiceConfig,
    load_config,
)


SAMPLE_DATA = {
    "location": {"name": "Moscow"},
    "current": {
        "temp_c": 5,
        "feelslike_c": 3,
        "condition": {"text": "Ясно"},
        "humidity": 60,
        "wind_kph": 12,
        "wind_dir": "NW",
    },
    "forecast": {
        "forecastday": [
            {
                "date": "2026-02-06",
                "day": {
                    "maxtemp_c": 6,
                    "mintemp_c": -2,
                    "daily_chance_of_rain": 10,
                    "daily_chance_of_snow": 0,
                    "uv": 2,
                    "condition": {"text": "Солнечно"},
                },
                "astro": {
                    "sunrise": "07:45 AM",
                    "sunset": "05:30 PM",
                    "moon_phase": "Waxing",
                },
                "hour": [
                    {
                        "time": "2026-02-06 07:00",
                        "temp_c": 2,
                        "condition": {"text": "Ясно"},
                    },
                    {
                        "time": "2026-02-06 08:00",
                        "temp_c": 3,
                        "condition": {"text": "Ясно"},
                    },
                ],
            },
            {
                "date": "2026-02-07",
                "day": {
                    "maxtemp_c": 4,
                    "mintemp_c": -4,
                    "daily_chance_of_rain": 20,
                    "daily_chance_of_snow": 10,
                    "uv": 1,
                    "condition": {"text": "Облачно"},
                },
            },
            {
                "date": "2026-02-08",
                "day": {
                    "maxtemp_c": 3,
                    "mintemp_c": -5,
                    "daily_chance_of_rain": 5,
                    "daily_chance_of_snow": 20,
                    "uv": 1,
                    "condition": {"text": "Снег"},
                },
            },
        ]
    },
}


class DummyApiClient:
    def __init__(self, data):
        self.data = data

    def get_forecast(self, city, days, lang):
        return self.data

    def get_current(self, city, lang):
        return {"location": {"name": city}, "current": {"temp_c": 0}}


class FailingApiClient:
    def get_forecast(self, city, days, lang):
        raise WeatherApiError("API unavailable")

    def get_current(self, city, lang):
        raise WeatherApiError("API unavailable")


class WeatherServiceTests(unittest.TestCase):
    def test_load_config_with_environment(self):
        config_yaml = """
environment: dev
defaults:
  language: "ru"
environments:
  dev:
    api_key: "dev-key"
    cities: ["Moscow"]
  prod:
    api_key: "prod-key"
    cities: ["Moscow", "Berlin"]
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml", delete=False) as handle:
            handle.write(config_yaml)
            handle.flush()
            path = handle.name
        try:
            config = load_config(path, env_override="prod")
            self.assertEqual(config.api_key, "prod-key")
            self.assertEqual(config.cities, ["Moscow", "Berlin"])
        finally:
            os.remove(path)

    def test_memory_cache_expiry(self):
        current_time = [1000.0]

        def time_provider():
            return current_time[0]

        cache = MemoryCache(ttl_seconds=10, time_provider=time_provider)
        cache.set("key", {"value": 1})
        self.assertEqual(cache.get("key"), {"value": 1})
        current_time[0] += 11
        self.assertIsNone(cache.get("key"))

    def test_significant_change_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            config = WeatherServiceConfig(
                api_key="dummy",
                cities=["Moscow"],
                last_sent_state_path=state_path,
            )
            service = WeatherService(
                config,
                api_client=DummyApiClient(SAMPLE_DATA),
                cache=MemoryCache(ttl_seconds=60),
                fallback_store=FileFallbackStore(os.path.join(tmp, "fallback.json")),
                rate_limiter=RateLimiter(max_requests=100),
            )
            snapshot = service._extract_snapshot(SAMPLE_DATA)
            self.assertTrue(service.should_send_notification("Moscow", snapshot))
            service.update_last_sent_state("Moscow", snapshot)
            self.assertFalse(service.should_send_notification("Moscow", snapshot))
            changed = dict(snapshot)
            changed["temp_c"] = 10
            self.assertTrue(service.should_send_notification("Moscow", changed))

    def test_format_telegram_message_contains_sections(self):
        config = WeatherServiceConfig(api_key="dummy", cities=["Moscow"])
        service = WeatherService(config, api_client=DummyApiClient(SAMPLE_DATA))
        message = service.format_message(SAMPLE_DATA, platform="telegram")
        self.assertIn("Доброе утро", message)
        self.assertIn("Погода в Moscow", message)
        self.assertIn("Почасовой прогноз", message)

    def test_fallback_on_api_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback_path = os.path.join(tmp, "fallback.json")
            fallback_store = FileFallbackStore(fallback_path)
            cache_key = "forecast:Moscow:3:ru"
            fallback_store.set(cache_key, SAMPLE_DATA)
            config = WeatherServiceConfig(
                api_key="dummy",
                cities=["Moscow"],
                fallback_cache_path=fallback_path,
                fallback_max_age_hours=24,
            )
            service = WeatherService(
                config,
                api_client=FailingApiClient(),
                cache=MemoryCache(ttl_seconds=1),
                fallback_store=fallback_store,
                rate_limiter=RateLimiter(max_requests=100),
            )
            result = service.fetch_forecast("Moscow")
            self.assertIsInstance(result, WeatherResult)
            self.assertEqual(result.source, "fallback")


if __name__ == "__main__":
    unittest.main()
