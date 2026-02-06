#!/usr/bin/env python3
"""
Improved weather service for OpenClaw.

Features:
- Caching with TTL (in-memory + persistent fallback)
- Retry with exponential backoff
- Rate limiting protection
- Configurable environments (dev/prod) via YAML/JSON
- Structured logging
- Multi-city support and 3-day forecast
- Smart notifications (only if weather changes significantly)
- Telegram / WhatsApp formatting
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


class WeatherServiceError(Exception):
    """Base class for weather service errors."""


class WeatherConfigError(WeatherServiceError):
    """Raised when configuration is invalid."""


class WeatherValidationError(WeatherServiceError):
    """Raised when input validation fails."""


class WeatherApiError(WeatherServiceError):
    """Raised for API failures."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class WeatherRateLimitError(WeatherServiceError):
    """Raised when rate limit is reached."""


@dataclass
class SignificantChangeConfig:
    temp_c_threshold: float = 3.0
    feelslike_c_threshold: float = 3.0
    rain_chance_threshold: int = 30
    wind_kph_threshold: float = 10.0
    condition_change: bool = True


@dataclass
class WeatherServiceConfig:
    api_key: str
    cities: List[str]
    base_url: str = "http://api.weatherapi.com/v1"
    language: str = "ru"
    cache_ttl_minutes: int = 30
    forecast_days: int = 3
    request_timeout: int = 10
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 8.0
    rate_limit_per_minute: int = 55
    fallback_cache_path: str = "/tmp/weather_api_cache.json"
    fallback_max_age_hours: int = 12
    last_sent_state_path: str = "/tmp/weather_last_sent.json"
    log_level: str = "INFO"
    significant_change: SignificantChangeConfig = field(
        default_factory=SignificantChangeConfig
    )

    def validate(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise WeatherConfigError("API key is required.")
        if not isinstance(self.cities, list) or not self.cities:
            raise WeatherConfigError("At least one city must be configured.")
        for city in self.cities:
            _validate_city_name(city)
        if self.forecast_days < 1 or self.forecast_days > 10:
            raise WeatherConfigError("forecast_days must be between 1 and 10.")
        if self.cache_ttl_minutes < 1:
            raise WeatherConfigError("cache_ttl_minutes must be >= 1.")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WeatherServiceConfig":
        sig = data.get("significant_change", {})
        significant_change = SignificantChangeConfig(
            temp_c_threshold=float(sig.get("temp_c_threshold", 3.0)),
            feelslike_c_threshold=float(sig.get("feelslike_c_threshold", 3.0)),
            rain_chance_threshold=int(sig.get("rain_chance_threshold", 30)),
            wind_kph_threshold=float(sig.get("wind_kph_threshold", 10.0)),
            condition_change=bool(sig.get("condition_change", True)),
        )
        return cls(
            api_key=str(data.get("api_key", "")),
            cities=list(data.get("cities", [])),
            base_url=str(data.get("base_url", "http://api.weatherapi.com/v1")),
            language=str(data.get("language", "ru")),
            cache_ttl_minutes=int(data.get("cache_ttl_minutes", 30)),
            forecast_days=int(data.get("forecast_days", 3)),
            request_timeout=int(data.get("request_timeout", 10)),
            max_retries=int(data.get("max_retries", 3)),
            backoff_base_seconds=float(data.get("backoff_base_seconds", 1.0)),
            backoff_max_seconds=float(data.get("backoff_max_seconds", 8.0)),
            rate_limit_per_minute=int(data.get("rate_limit_per_minute", 55)),
            fallback_cache_path=str(
                data.get("fallback_cache_path", "/tmp/weather_api_cache.json")
            ),
            fallback_max_age_hours=int(data.get("fallback_max_age_hours", 12)),
            last_sent_state_path=str(
                data.get("last_sent_state_path", "/tmp/weather_last_sent.json")
            ),
            log_level=str(data.get("log_level", "INFO")),
            significant_change=significant_change,
        )


@dataclass
class CacheEntry:
    data: Dict[str, Any]
    expires_at: float
    stored_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class WeatherResult:
    data: Dict[str, Any]
    source: str
    is_stale: bool
    fetched_at: datetime


class MemoryCache:
    def __init__(
        self,
        ttl_seconds: int,
        time_provider: Callable[[], float] = time.time,
        max_entries: int = 512,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.time_provider = time_provider
        self.max_entries = max_entries
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def get_entry(self, key: str) -> Optional[CacheEntry]:
        now = self.time_provider()
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if entry.is_expired(now):
                return entry
            return entry

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        now = self.time_provider()
        with self._lock:
            entry = self._cache.get(key)
            if not entry or entry.is_expired(now):
                return None
            return entry.data

    def set(self, key: str, data: Dict[str, Any]) -> None:
        now = self.time_provider()
        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._evict_one()
            self._cache[key] = CacheEntry(
                data=data, stored_at=now, expires_at=now + self.ttl_seconds
            )

    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def _evict_one(self) -> None:
        oldest_key = None
        oldest_time = None
        for key, entry in self._cache.items():
            if oldest_time is None or entry.stored_at < oldest_time:
                oldest_time = entry.stored_at
                oldest_key = key
        if oldest_key:
            self._cache.pop(oldest_key, None)


class FileFallbackStore:
    def __init__(
        self,
        path: str,
        time_provider: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.time_provider = time_provider
        self._lock = threading.Lock()

    def get(self, key: str, max_age_seconds: Optional[int] = None) -> Optional[Dict[str, Any]]:
        now = self.time_provider()
        with self._lock:
            payload = self._load()
            entry = payload.get(key)
            if not entry:
                return None
            stored_at = entry.get("stored_at", 0)
            if max_age_seconds is not None and now - stored_at > max_age_seconds:
                return None
            return entry.get("data")

    def set(self, key: str, data: Dict[str, Any]) -> None:
        now = self.time_provider()
        with self._lock:
            payload = self._load()
            payload[key] = {"stored_at": now, "data": data}
            self._save(payload)

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _save(self, payload: Dict[str, Any]) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp_path, self.path)


class RateLimiter:
    def __init__(
        self,
        max_requests: int,
        window_seconds: int = 60,
        time_provider: Callable[[], float] = time.time,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.time_provider = time_provider
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self.time_provider()
        with self._lock:
            while self._events and now - self._events[0] >= self.window_seconds:
                self._events.popleft()
            if len(self._events) >= self.max_requests:
                return False
            self._events.append(now)
            return True


class WeatherApiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int,
        max_retries: int,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
        rate_limiter: Optional[RateLimiter] = None,
        logger: Optional[logging.Logger] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.rate_limiter = rate_limiter
        self.logger = logger or logging.getLogger(__name__)
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "OpenClaw-WeatherService/1.0"})

    def get_forecast(self, city: str, days: int, lang: str) -> Dict[str, Any]:
        params = {"key": self.api_key, "q": city, "days": days, "lang": lang}
        return self._request("forecast.json", params)

    def get_current(self, city: str, lang: str) -> Dict[str, Any]:
        params = {"key": self.api_key, "q": city, "lang": lang}
        return self._request("current.json", params)

    def _request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.rate_limiter and not self.rate_limiter.allow():
            raise WeatherRateLimitError("Rate limit exceeded.")

        url = f"{self.base_url}/{endpoint}"
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    sleep_for = self._get_retry_sleep(attempt, retry_after)
                    log_event(
                        self.logger,
                        "warning",
                        "rate_limited",
                        status_code=429,
                        retry_after=retry_after,
                        sleep_for=sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                if 400 <= response.status_code < 500:
                    raise WeatherApiError(
                        f"API error {response.status_code}: {response.text}",
                        retryable=False,
                    )
                if response.status_code >= 500:
                    raise WeatherApiError(f"API unavailable {response.status_code}")
                data = json.loads(response.content)
                return data
            except (requests.RequestException, json.JSONDecodeError, WeatherApiError) as exc:
                last_error = exc
                if isinstance(exc, WeatherApiError) and not exc.retryable:
                    break
                if attempt >= self.max_retries:
                    break
                sleep_for = min(
                    self.backoff_base_seconds * (2 ** (attempt - 1)),
                    self.backoff_max_seconds,
                )
                log_event(
                    self.logger,
                    "warning",
                    "request_retry",
                    attempt=attempt,
                    sleep_for=sleep_for,
                    error=str(exc),
                )
                time.sleep(sleep_for)

        raise WeatherApiError(f"Weather API request failed: {last_error}")

    def _get_retry_sleep(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return min(
            self.backoff_base_seconds * (2 ** (attempt - 1)),
            self.backoff_max_seconds,
        )


class WeatherService:
    def __init__(
        self,
        config: WeatherServiceConfig,
        api_client: Optional[WeatherApiClient] = None,
        cache: Optional[MemoryCache] = None,
        fallback_store: Optional[FileFallbackStore] = None,
        rate_limiter: Optional[RateLimiter] = None,
        logger: Optional[logging.Logger] = None,
        time_provider: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.config.validate()
        self.time_provider = time_provider
        self.logger = logger or setup_logger(self.config.log_level)
        self.cache = cache or MemoryCache(
            ttl_seconds=self.config.cache_ttl_minutes * 60,
            time_provider=self.time_provider,
        )
        self.fallback_store = fallback_store or FileFallbackStore(
            self.config.fallback_cache_path, time_provider=self.time_provider
        )
        self.rate_limiter = rate_limiter or RateLimiter(
            max_requests=self.config.rate_limit_per_minute,
            time_provider=self.time_provider,
        )
        self.api_client = api_client or WeatherApiClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.request_timeout,
            max_retries=self.config.max_retries,
            backoff_base_seconds=self.config.backoff_base_seconds,
            backoff_max_seconds=self.config.backoff_max_seconds,
            rate_limiter=self.rate_limiter,
            logger=self.logger,
        )
        self._last_sent_lock = threading.Lock()
        self._last_successful_api_at: Optional[float] = None

    def fetch_forecast(self, city: str) -> WeatherResult:
        sanitized = sanitize_city_name(city)
        cache_key = self._cache_key(sanitized)
        now = datetime.now()
        entry = self.cache.get_entry(cache_key)
        if entry and not entry.is_expired(self.time_provider()):
            log_event(
                self.logger,
                "info",
                "cache_hit",
                city=sanitized,
                source="memory",
            )
            return WeatherResult(
                data=entry.data, source="cache", is_stale=False, fetched_at=now
            )

        try:
            data = self.api_client.get_forecast(
                city=sanitized,
                days=self.config.forecast_days,
                lang=self.config.language,
            )
            self.cache.set(cache_key, data)
            self.fallback_store.set(cache_key, data)
            self._last_successful_api_at = self.time_provider()
            log_event(self.logger, "info", "api_success", city=sanitized)
            return WeatherResult(
                data=data, source="api", is_stale=False, fetched_at=now
            )
        except WeatherServiceError as exc:
            log_event(
                self.logger,
                "warning",
                "api_failure",
                city=sanitized,
                error=str(exc),
            )

        if entry:
            log_event(
                self.logger,
                "warning",
                "using_stale_cache",
                city=sanitized,
                source="memory",
            )
            return WeatherResult(
                data=entry.data, source="cache", is_stale=True, fetched_at=now
            )

        fallback_data = self.fallback_store.get(
            cache_key,
            max_age_seconds=self.config.fallback_max_age_hours * 3600,
        )
        if fallback_data:
            log_event(
                self.logger,
                "warning",
                "using_fallback",
                city=sanitized,
                source="disk",
            )
            return WeatherResult(
                data=fallback_data, source="fallback", is_stale=True, fetched_at=now
            )

        raise WeatherApiError("No data available from API or fallback.")

    def build_daily_message(
        self,
        platform: str = "telegram",
        force_send: bool = False,
    ) -> Optional[str]:
        messages: List[str] = []
        greeting_included = False
        for city in self.config.cities:
            result = self.fetch_forecast(city)
            snapshot = self._extract_snapshot(result.data)
            if not force_send and not self.should_send_notification(city, snapshot):
                log_event(
                    self.logger,
                    "info",
                    "skip_notification",
                    city=city,
                    reason="no_significant_change",
                )
                continue
            message = self.format_message(
                result.data,
                platform=platform,
                include_greeting=not greeting_included,
            )
            greeting_included = True
            messages.append(message)
            self.update_last_sent_state(city, snapshot)

        if not messages:
            return None
        return self._combine_messages(messages)

    def format_message(
        self,
        data: Dict[str, Any],
        platform: str = "telegram",
        include_greeting: bool = True,
    ) -> str:
        platform_normalized = platform.lower().strip()
        if platform_normalized == "telegram":
            return self._format_telegram_message(
                data=data, include_greeting=include_greeting
            )
        if platform_normalized == "whatsapp":
            return self._format_whatsapp_message(
                data=data, include_greeting=include_greeting
            )
        raise WeatherValidationError(f"Unsupported platform: {platform}")

    def should_send_notification(
        self, city: str, snapshot: Dict[str, Any]
    ) -> bool:
        if not snapshot:
            return True
        city_key = sanitize_city_name(city)
        state = self._load_last_sent_state()
        previous = state.get(city_key)
        if not previous:
            return True
        return self._is_significant_change(previous, snapshot)

    def update_last_sent_state(self, city: str, snapshot: Dict[str, Any]) -> None:
        city_key = sanitize_city_name(city)
        with self._last_sent_lock:
            state = self._load_last_sent_state()
            state[city_key] = snapshot
            self._save_last_sent_state(state)

    def health_check(self) -> Dict[str, Any]:
        status = "ok"
        errors: List[str] = []
        api_reachable = False
        try:
            city = self.config.cities[0]
            self.api_client.get_current(city=city, lang=self.config.language)
            api_reachable = True
        except WeatherServiceError as exc:
            status = "degraded"
            errors.append(str(exc))
        return {
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "api_reachable": api_reachable,
            "cache_entries": self.cache.size(),
            "last_successful_api_at": (
                datetime.fromtimestamp(self._last_successful_api_at).isoformat()
                if self._last_successful_api_at
                else None
            ),
            "errors": errors,
        }

    def _cache_key(self, city: str) -> str:
        return f"forecast:{city}:{self.config.forecast_days}:{self.config.language}"

    def _combine_messages(self, messages: Iterable[str]) -> str:
        messages_list = list(messages)
        if len(messages_list) == 1:
            return messages_list[0]
        separator = "\n\n----------------\n\n"
        return separator.join(messages_list)

    def _load_last_sent_state(self) -> Dict[str, Any]:
        path = self.config.last_sent_state_path
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _save_last_sent_state(self, state: Dict[str, Any]) -> None:
        path = self.config.last_sent_state_path
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False)
        os.replace(tmp_path, path)

    def _is_significant_change(
        self, previous: Dict[str, Any], current: Dict[str, Any]
    ) -> bool:
        config = self.config.significant_change
        for key in ("temp_c", "feelslike_c", "wind_kph"):
            prev_value = _to_float(previous.get(key))
            curr_value = _to_float(current.get(key))
            if prev_value is None or curr_value is None:
                return True
            threshold = (
                config.temp_c_threshold
                if key == "temp_c"
                else config.feelslike_c_threshold
                if key == "feelslike_c"
                else config.wind_kph_threshold
            )
            if abs(curr_value - prev_value) >= threshold:
                return True

        prev_rain = _to_float(previous.get("rain_chance"))
        curr_rain = _to_float(current.get("rain_chance"))
        if prev_rain is None or curr_rain is None:
            return True
        if abs(curr_rain - prev_rain) >= config.rain_chance_threshold:
            return True

        if config.condition_change:
            if previous.get("condition") != current.get("condition"):
                return True

        return False

    def _extract_snapshot(self, data: Dict[str, Any]) -> Dict[str, Any]:
        current = data.get("current", {})
        today = _safe_get(data, "forecast", "forecastday", 0, default={})
        return {
            "temp_c": current.get("temp_c"),
            "feelslike_c": current.get("feelslike_c"),
            "condition": _safe_get(current, "condition", "text"),
            "rain_chance": _safe_get(today, "day", "daily_chance_of_rain"),
            "wind_kph": current.get("wind_kph"),
        }

    def _format_telegram_message(
        self, data: Dict[str, Any], include_greeting: bool = True
    ) -> str:
        location = _safe_get(data, "location", "name") or "Unknown"
        current = data.get("current", {})
        forecast_days = _safe_get(data, "forecast", "forecastday", default=[])
        today = forecast_days[0] if forecast_days else {}
        tomorrow = forecast_days[1] if len(forecast_days) > 1 else None

        date_label = today.get("date") or datetime.now().strftime("%d.%m.%Y")
        if len(date_label) == 10 and "-" in date_label:
            date_label = datetime.strptime(date_label, "%Y-%m-%d").strftime("%d.%m.%Y")

        message = ""
        if include_greeting:
            message += "🌅 **Доброе утро!** 🌤️\n\n"
        message += f"**Погода в {location} на {date_label}**\n\n"

        message += "📊 **Сейчас за окном:**\n"
        message += (
            f"• 🌡️ {_display(current.get('temp_c'))}°C "
            f"(ощущается как {_display(current.get('feelslike_c'))}°C)\n"
        )
        message += f"• ☁️ {_display(_safe_get(current, 'condition', 'text'))}\n"
        message += f"• 💧 Влажность: {_display(current.get('humidity'))}%\n"
        message += (
            f"• 💨 Ветер: {_display(current.get('wind_kph'))} км/ч, "
            f"{_display(current.get('wind_dir'))}\n\n"
        )

        message += "📅 **Прогноз на сегодня:**\n"
        message += f"• ☀️ Максимум: {_display(_safe_get(today, 'day', 'maxtemp_c'))}°C\n"
        message += f"• 🌙 Минимум: {_display(_safe_get(today, 'day', 'mintemp_c'))}°C\n"
        message += (
            f"• 🌧️ Дождь: "
            f"{_display(_safe_get(today, 'day', 'daily_chance_of_rain'))}%\n"
        )
        message += (
            f"• ❄️ Снег: "
            f"{_display(_safe_get(today, 'day', 'daily_chance_of_snow'))}%\n"
        )
        message += f"• ☀️ УФ-индекс: {_display(_safe_get(today, 'day', 'uv'))}\n\n"

        message += "⏰ **Световой день:**\n"
        message += f"• 🌅 Восход: {_display(_safe_get(today, 'astro', 'sunrise'))}\n"
        message += f"• 🌇 Закат: {_display(_safe_get(today, 'astro', 'sunset'))}\n"
        message += f"• 🌝 Фаза луны: {_display(_safe_get(today, 'astro', 'moon_phase'))}\n\n"

        message += "🕐 **Почасовой прогноз:**\n"
        hours = today.get("hour", [])
        if hours:
            for hour in hours[:6]:
                time_label = str(hour.get("time", "")).split()
                time_label = time_label[1][:5] if len(time_label) > 1 else "??:??"
                message += (
                    f"• {time_label}: {_display(hour.get('temp_c'))}°C, "
                    f"{_display(_safe_get(hour, 'condition', 'text'))}\n"
                )
        else:
            message += "• нет данных\n"

        if tomorrow:
            message += f"\n🔮 **Завтра ({_display(tomorrow.get('date'))}):**\n"
            message += (
                f"• ☀️ {_display(_safe_get(tomorrow, 'day', 'maxtemp_c'))}°C / "
                f"🌙 {_display(_safe_get(tomorrow, 'day', 'mintemp_c'))}°C\n"
            )
            message += f"• {_display(_safe_get(tomorrow, 'day', 'condition', 'text'))}\n"

        if forecast_days:
            message += "\n📆 **Прогноз на 3 дня:**\n"
            for day in forecast_days[:3]:
                day_date = _display(day.get("date"))
                day_max = _display(_safe_get(day, "day", "maxtemp_c"))
                day_min = _display(_safe_get(day, "day", "mintemp_c"))
                day_condition = _display(_safe_get(day, "day", "condition", "text"))
                message += f"• {day_date}: ☀️ {day_max}°C / 🌙 {day_min}°C, {day_condition}\n"

        message += (
            f"\n📡 _Данные: WeatherAPI.com • "
            f"{datetime.now().strftime('%H:%M')}_"
        )
        return message

    def _format_whatsapp_message(
        self, data: Dict[str, Any], include_greeting: bool = True
    ) -> str:
        location = _safe_get(data, "location", "name") or "Unknown"
        current = data.get("current", {})
        forecast_days = _safe_get(data, "forecast", "forecastday", default=[])
        today = forecast_days[0] if forecast_days else {}
        tomorrow = forecast_days[1] if len(forecast_days) > 1 else None

        date_label = today.get("date") or datetime.now().strftime("%d.%m.%Y")
        if len(date_label) == 10 and "-" in date_label:
            date_label = datetime.strptime(date_label, "%Y-%m-%d").strftime("%d.%m.%Y")

        message = ""
        if include_greeting:
            message += "Доброе утро!\n\n"
        message += f"Погода в {location} на {date_label}\n\n"

        message += "Сейчас за окном:\n"
        message += (
            f"- Температура: {_display(current.get('temp_c'))}°C "
            f"(ощущается как {_display(current.get('feelslike_c'))}°C)\n"
        )
        message += f"- Состояние: {_display(_safe_get(current, 'condition', 'text'))}\n"
        message += f"- Влажность: {_display(current.get('humidity'))}%\n"
        message += (
            f"- Ветер: {_display(current.get('wind_kph'))} км/ч, "
            f"{_display(current.get('wind_dir'))}\n\n"
        )

        message += "Прогноз на сегодня:\n"
        message += f"- Максимум: {_display(_safe_get(today, 'day', 'maxtemp_c'))}°C\n"
        message += f"- Минимум: {_display(_safe_get(today, 'day', 'mintemp_c'))}°C\n"
        message += (
            f"- Дождь: {_display(_safe_get(today, 'day', 'daily_chance_of_rain'))}%\n"
        )
        message += (
            f"- Снег: {_display(_safe_get(today, 'day', 'daily_chance_of_snow'))}%\n"
        )
        message += f"- УФ-индекс: {_display(_safe_get(today, 'day', 'uv'))}\n\n"

        message += "Световой день:\n"
        message += f"- Восход: {_display(_safe_get(today, 'astro', 'sunrise'))}\n"
        message += f"- Закат: {_display(_safe_get(today, 'astro', 'sunset'))}\n"
        message += f"- Фаза луны: {_display(_safe_get(today, 'astro', 'moon_phase'))}\n\n"

        message += "Почасовой прогноз:\n"
        hours = today.get("hour", [])
        if hours:
            for hour in hours[:6]:
                time_label = str(hour.get("time", "")).split()
                time_label = time_label[1][:5] if len(time_label) > 1 else "??:??"
                message += (
                    f"- {time_label}: {_display(hour.get('temp_c'))}°C, "
                    f"{_display(_safe_get(hour, 'condition', 'text'))}\n"
                )
        else:
            message += "- нет данных\n"

        if tomorrow:
            message += f"\nЗавтра ({_display(tomorrow.get('date'))}):\n"
            message += (
                f"- {_display(_safe_get(tomorrow, 'day', 'maxtemp_c'))}°C / "
                f"{_display(_safe_get(tomorrow, 'day', 'mintemp_c'))}°C\n"
            )
            message += f"- {_display(_safe_get(tomorrow, 'day', 'condition', 'text'))}\n"

        if forecast_days:
            message += "\nПрогноз на 3 дня:\n"
            for day in forecast_days[:3]:
                day_date = _display(day.get("date"))
                day_max = _display(_safe_get(day, "day", "maxtemp_c"))
                day_min = _display(_safe_get(day, "day", "mintemp_c"))
                day_condition = _display(_safe_get(day, "day", "condition", "text"))
                message += f"- {day_date}: {day_max}°C / {day_min}°C, {day_condition}\n"

        message += f"\nДанные: WeatherAPI.com • {datetime.now().strftime('%H:%M')}"
        return message


def setup_logger(log_level: str) -> logging.Logger:
    logger = logging.getLogger("weather_service")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, level: str, event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    message = json.dumps(payload, ensure_ascii=False)
    log_fn = getattr(logger, level, logger.info)
    log_fn(message)


def sanitize_city_name(city: str) -> str:
    if city is None:
        raise WeatherValidationError("City cannot be null.")
    cleaned = str(city).strip()
    _validate_city_name(cleaned)
    return cleaned


def _validate_city_name(city: str) -> None:
    if not city:
        raise WeatherValidationError("City name cannot be empty.")
    if len(city) > 128:
        raise WeatherValidationError("City name is too long.")
    if not re.match(r"^[\w\s\-\.\,]+$", city, flags=re.UNICODE):
        raise WeatherValidationError("City name contains invalid characters.")


def _safe_get(data: Any, *keys: Any, default: Any = None) -> Any:
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return default
    return current


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display(value: Any, default: str = "н/д") -> str:
    if value is None:
        return default
    return str(value)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _expand_env_vars(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    if isinstance(value, str):
        pattern = re.compile(r"\$\{([^}]+)\}")
        matches = pattern.findall(value)
        for match in matches:
            replacement = os.getenv(match, "")
            value = value.replace(f"${{{match}}}", replacement)
        return value
    return value


def load_config(config_path: str, env_override: Optional[str] = None) -> WeatherServiceConfig:
    if not os.path.exists(config_path):
        raise WeatherConfigError(f"Config file not found: {config_path}")
    raw: Dict[str, Any] = {}
    with open(config_path, "r", encoding="utf-8") as handle:
        if config_path.endswith((".yaml", ".yml")):
            if not yaml:
                raise WeatherConfigError("PyYAML is required to read YAML configs.")
            raw = yaml.safe_load(handle) or {}
        elif config_path.endswith(".json"):
            raw = json.load(handle)
        else:
            raise WeatherConfigError("Config must be YAML or JSON.")

    env_name = env_override or os.getenv("WEATHER_ENV") or raw.get("environment") or "dev"
    defaults = raw.get("defaults", {})
    environments = raw.get("environments")
    if isinstance(environments, dict):
        env_data = environments.get(env_name, {})
        merged = _deep_merge(defaults, env_data)
    else:
        merged = _deep_merge(defaults, raw)
    expanded = _expand_env_vars(merged)
    config = WeatherServiceConfig.from_dict(expanded)
    config.validate()
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw improved weather service")
    parser.add_argument(
        "--config",
        default=os.getenv("WEATHER_CONFIG_PATH", "/workspace/weather_service_config.yaml"),
        help="Path to YAML/JSON configuration file",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="Environment name override (dev/prod)",
    )
    parser.add_argument(
        "--platform",
        default="telegram",
        choices=["telegram", "whatsapp"],
        help="Output format platform",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Run health check and output JSON",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force sending message even if no significant change",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to save message output",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config, env_override=args.env)
        service = WeatherService(config)

        if args.health_check:
            payload = service.health_check()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        message = service.build_daily_message(
            platform=args.platform,
            force_send=args.force,
        )
        if not message:
            print("No significant weather changes detected.")
            return 0

        print(message)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(message)
        return 0
    except WeatherServiceError as exc:
        print(f"Weather service error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
