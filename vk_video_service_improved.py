from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import requests

from config import VKVideoSearchConfig, load_vk_config

logger = logging.getLogger(__name__)


class VKVideoSearchError(Exception):
    """Base error for VK video search service."""


class VKInvalidQueryError(VKVideoSearchError):
    """Raised when a query is invalid or empty."""


class VKAuthorizationError(VKVideoSearchError):
    """Raised when VK API authorization fails."""


class VKRateLimitError(VKVideoSearchError):
    """Raised when VK API rate limits are hit."""


class VKApiError(VKVideoSearchError):
    """Raised for VK API errors."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.details = details


@dataclass(frozen=True)
class VideoResult:
    id: int
    owner_id: int
    title: str
    description: str
    duration: int
    views: int
    date: int
    url: str
    preview_url: str
    tags: Tuple[str, ...] = ()
    access_key: Optional[str] = None
    source: str = "vk"


@dataclass
class SearchFilters:
    min_duration: Optional[int] = None
    max_duration: Optional[int] = None
    min_views: Optional[int] = None
    max_views: Optional[int] = None
    min_date: Optional[int] = None
    max_date: Optional[int] = None
    tags: Tuple[str, ...] = ()
    categories: Tuple[str, ...] = ()
    match_all_tags: bool = False


@dataclass(frozen=True)
class PlaylistRef:
    owner_id: int
    album_id: int

    @staticmethod
    def from_string(value: str) -> "PlaylistRef":
        parts = value.strip().split("_")
        if len(parts) != 2:
            raise ValueError("Invalid playlist reference format")
        return PlaylistRef(owner_id=int(parts[0]), album_id=int(parts[1]))


@dataclass(frozen=True)
class SearchSources:
    include_global: bool = True
    group_ids: Tuple[int, ...] = ()
    playlists: Tuple[PlaylistRef, ...] = ()


@dataclass
class SearchResponse:
    items: List[VideoResult]
    total: int
    source: str
    cached: bool = False
    error: Optional[str] = None


@dataclass(frozen=True)
class SearchRecord:
    query: str
    timestamp: float
    filters: SearchFilters
    result_ids: Tuple[str, ...]


@dataclass
class Metrics:
    api_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    rate_limit_waits: int = 0
    auth_errors: int = 0
    api_errors: int = 0
    last_error: Optional[str] = None
    last_call_time: Optional[float] = None


class LRUTTLCache:
    """In-memory LRU cache with TTL."""

    def __init__(self, max_entries: int, ttl_seconds: int) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._data: "OrderedDict[str, Tuple[Any, float]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if not item:
                self.misses += 1
                return None
            value, expires_at = item
            if expires_at and expires_at < time.time():
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            expires_at = time.time() + self._ttl_seconds if self._ttl_seconds else 0
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    def size(self) -> int:
        with self._lock:
            return len(self._data)

    def stats(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": self.size()}


class RateLimiter:
    """Simple rate limiter enforcing max requests per second."""

    def __init__(self, rate_per_sec: float) -> None:
        self._rate_per_sec = rate_per_sec
        self._min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._last_request_time = 0.0
        self.total_wait = 0.0

    def wait(self) -> float:
        if self._min_interval <= 0:
            return 0.0
        wait_time = 0.0
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                wait_time = self._min_interval - elapsed
                self._last_request_time = now + wait_time
            else:
                self._last_request_time = now
        if wait_time > 0:
            time.sleep(wait_time)
        self.total_wait += wait_time
        return wait_time


class TokenRotator:
    """Round-robin token rotation with cooldown for invalid tokens."""

    def __init__(self, tokens: Iterable[str], cooldown_sec: int) -> None:
        self._tokens = [token for token in tokens if token]
        self._cooldown_sec = cooldown_sec
        self._invalid_until: Dict[str, float] = {}
        self._index = 0
        self._lock = threading.Lock()

    @property
    def tokens(self) -> List[str]:
        return list(self._tokens)

    def next_token(self) -> str:
        if not self._tokens:
            raise VKAuthorizationError("No VK access tokens configured.")
        now = time.time()
        with self._lock:
            for _ in range(len(self._tokens)):
                token = self._tokens[self._index % len(self._tokens)]
                self._index += 1
                invalid_until = self._invalid_until.get(token, 0)
                if invalid_until <= now:
                    return token
        raise VKAuthorizationError("All VK tokens are in cooldown.")

    def mark_invalid(self, token: str) -> None:
        with self._lock:
            self._invalid_until[token] = time.time() + self._cooldown_sec


class VKApiClient:
    """Minimal VK API client with rate limiting and token rotation."""

    def __init__(
        self,
        config: VKVideoSearchConfig,
        rate_limiter: Optional[RateLimiter] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._config = config
        self._rate_limiter = rate_limiter or RateLimiter(config.rate_limit_per_sec)
        self._session = session or requests.Session()
        self._token_rotator = TokenRotator(config.access_tokens, config.auth_error_cooldown_sec)
        self.metrics = Metrics()

    def call(
        self,
        method: str,
        params: Dict[str, Any],
        token_override: Optional[str] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        if token_override:
            return self._call_with_token(method, params, token_override, max_retries)

        tokens = self._token_rotator.tokens
        last_error: Optional[Exception] = None
        attempts = max(1, len(tokens))

        for _ in range(attempts):
            token = self._token_rotator.next_token()
            try:
                return self._call_with_token(method, params, token, max_retries)
            except VKAuthorizationError as exc:
                self._token_rotator.mark_invalid(token)
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise VKAuthorizationError("No valid VK token available.")

    def _call_with_token(
        self,
        method: str,
        params: Dict[str, Any],
        token: str,
        max_retries: int,
    ) -> Dict[str, Any]:
        url = f"https://api.vk.com/method/{method}"
        payload = dict(params)
        payload["access_token"] = token
        payload["v"] = self._config.api_version

        for attempt in range(max_retries + 1):
            waited = self._rate_limiter.wait()
            if waited:
                self.metrics.rate_limit_waits += 1

            try:
                response = self._session.get(
                    url,
                    params=payload,
                    timeout=self._config.request_timeout_sec,
                )
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError) as exc:
                self.metrics.api_errors += 1
                self.metrics.last_error = str(exc)
                if attempt < max_retries:
                    time.sleep(0.3 * (2**attempt))
                    continue
                raise VKApiError("Network or JSON error", details=str(exc)) from exc

            if "error" in data:
                error = data["error"]
                error_code = error.get("error_code")
                error_msg = error.get("error_msg", "VK API error")
                if error_code == 5:
                    self.metrics.auth_errors += 1
                    raise VKAuthorizationError(error_msg)
                if error_code in (6, 29):
                    self.metrics.rate_limit_waits += 1
                    if attempt < max_retries:
                        time.sleep(0.34 * (2**attempt))
                        continue
                    raise VKRateLimitError(error_msg)
                self.metrics.api_errors += 1
                raise VKApiError(error_msg, details=error)

            if "response" not in data:
                self.metrics.api_errors += 1
                raise VKApiError("Malformed VK response", details=data)

            self.metrics.api_calls += 1
            self.metrics.last_call_time = time.time()
            return data["response"]

        raise VKApiError("VK request failed after retries.")


class SearchHistoryStore:
    """In-memory search history store."""

    def __init__(self, max_entries_per_user: int = 100) -> None:
        self._history: Dict[str, Deque[SearchRecord]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries_per_user

    def add_record(self, user_id: str, record: SearchRecord) -> None:
        with self._lock:
            if user_id not in self._history:
                self._history[user_id] = deque(maxlen=self._max_entries)
            self._history[user_id].append(record)

    def get_history(self, user_id: str, limit: Optional[int] = None) -> List[SearchRecord]:
        with self._lock:
            records = list(self._history.get(user_id, []))
        if limit:
            return records[-limit:]
        return records


def sanitize_query(query: str, max_len: int = 200) -> str:
    """Validate and sanitize search query."""

    if query is None:
        raise VKInvalidQueryError("Query is required.")
    if not isinstance(query, str):
        query = str(query)
    query = re.sub(r"[\x00-\x1f\x7f]+", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    if not query:
        raise VKInvalidQueryError("Query is empty.")
    if len(query) > max_len:
        query = query[:max_len].rstrip()
    if not re.search(r"[\w\d]", query):
        raise VKInvalidQueryError("Query contains no searchable characters.")
    return query


def _normalize_tags(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(tag.strip().lower() for tag in values if tag and tag.strip())


def _hash_key(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _select_preview(image_list: Any) -> str:
    if not image_list:
        return ""
    if isinstance(image_list, list):
        best = max(
            image_list,
            key=lambda item: (item.get("width", 0), item.get("height", 0)),
        )
        return best.get("url", "")
    return ""


def _format_duration(seconds: int) -> str:
    minutes, sec = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:d}:{sec:02d}"


def _tokenize(text: str) -> List[str]:
    return [token for token in re.split(r"[^\w]+", text.lower()) if token]


class VKVideoSearchService:
    """Production-ready VK video search service."""

    def __init__(
        self,
        config: Optional[VKVideoSearchConfig] = None,
        api_client: Optional[VKApiClient] = None,
        cache: Optional[LRUTTLCache] = None,
        history_store: Optional[SearchHistoryStore] = None,
    ) -> None:
        self._config = config or load_vk_config()
        self._api = api_client or VKApiClient(self._config)
        self._cache = cache or LRUTTLCache(
            max_entries=self._config.cache_max_entries,
            ttl_seconds=self._config.cache_ttl_seconds,
        )
        self._history = history_store or SearchHistoryStore()
        self.metrics = Metrics()

    def search(
        self,
        query: str,
        count: int = 10,
        offset: int = 0,
        filters: Optional[SearchFilters] = None,
        sort_by: str = "relevance",
        sources: Optional[SearchSources] = None,
        user_id: Optional[str] = None,
        use_cache: bool = True,
        record_history: bool = True,
    ) -> SearchResponse:
        sanitized = sanitize_query(query)
        filters = filters or SearchFilters()
        sources = sources or SearchSources()
        normalized_sort = self._normalize_sort(sort_by)
        cache_key = self._build_cache_key(
            sanitized,
            count,
            offset,
            filters,
            normalized_sort,
            sources,
        )

        if use_cache:
            cached = self._cache.get(cache_key)
            if cached:
                self.metrics.cache_hits += 1
                return SearchResponse(
                    items=cached["items"],
                    total=cached["total"],
                    source=cached["source"],
                    cached=True,
                )
            self.metrics.cache_misses += 1

        try:
            items, total = self._search_sources(
                sanitized,
                count=count,
                offset=offset,
                filters=filters,
                sort_by=normalized_sort,
                sources=sources,
            )
            response = SearchResponse(items=items, total=total, source="vk")
        except VKVideoSearchError as exc:
            logger.exception("VK search failed: %s", exc)
            response = SearchResponse(items=[], total=0, source="vk", error=str(exc))

        if use_cache and not response.error:
            self._cache.set(
                cache_key,
                {"items": response.items, "total": response.total, "source": response.source},
            )

        if record_history and user_id and not response.error:
            record = SearchRecord(
                query=sanitized,
                timestamp=time.time(),
                filters=filters,
                result_ids=tuple(self._result_key(item) for item in response.items),
            )
            self._history.add_record(user_id, record)

        return response

    def batch_search(
        self,
        queries: Iterable[str],
        per_query_count: int = 5,
        sort_by: str = "relevance",
    ) -> List[SearchResponse]:
        sanitized_queries = [sanitize_query(q) for q in queries if q]
        if not sanitized_queries:
            return []
        if len(sanitized_queries) > 25:
            sanitized_queries = sanitized_queries[:25]

        sort_by = self._normalize_sort(sort_by)
        script = {
            "queries": sanitized_queries,
            "count": per_query_count,
            "sort": self._vk_sort_param(sort_by),
        }
        code = (
            "var queries = %(queries)s;"
            "var count = %(count)d;"
            "var sort = %(sort)d;"
            "var results = [];"
            "var i = 0;"
            "while (i < queries.length) {"
            "results.push(API.video.search({q: queries[i], count: count, sort: sort}));"
            "i = i + 1;"
            "}"
            "return results;"
        ) % {
            "queries": json.dumps(script["queries"]),
            "count": script["count"],
            "sort": script["sort"],
        }

        response = self._api.call("execute", {"code": code})
        results: List[SearchResponse] = []
        for index, item in enumerate(response):
            items_raw = item.get("items", []) if isinstance(item, dict) else []
            normalized = [self._normalize_video(v, source="vk") for v in items_raw]
            results.append(
                SearchResponse(
                    items=normalized,
                    total=item.get("count", len(normalized)) if isinstance(item, dict) else 0,
                    source="vk",
                )
            )
        return results

    def get_history(self, user_id: str, limit: int = 20) -> List[SearchRecord]:
        return self._history.get_history(user_id, limit=limit)

    def get_recommendations(self, user_id: str, count: int = 10) -> SearchResponse:
        history = self._history.get_history(user_id, limit=50)
        keywords = self._extract_keywords(history)
        if not keywords:
            return self.get_trending(count=count)
        query = " ".join(keywords[:5])
        return self.search(
            query=query,
            count=count,
            sort_by="popularity",
            user_id=user_id,
            record_history=False,
        )

    def get_related_videos(self, video: VideoResult, count: int = 10) -> SearchResponse:
        params = {
            "owner_id": video.owner_id,
            "video_id": video.id,
            "count": count,
        }
        try:
            response = self._api.call("video.getRecommendations", params)
            items_raw = response.get("items", [])
            items = [self._normalize_video(v, source="vk") for v in items_raw]
            return SearchResponse(items=items, total=response.get("count", len(items)), source="vk")
        except VKVideoSearchError:
            keywords = self._extract_keywords_from_text(video.title)
            if not keywords:
                return SearchResponse(items=[], total=0, source="vk")
            query = " ".join(keywords[:5])
            return self.search(query=query, count=count, sort_by="relevance", record_history=False)

    def get_trending(self, count: int = 10) -> SearchResponse:
        params = {"count": count, "extended": 1}
        try:
            response = self._api.call("video.getPopular", params)
            items_raw = response.get("items", [])
            items = [self._normalize_video(v, source="vk") for v in items_raw]
            return SearchResponse(items=items, total=response.get("count", len(items)), source="vk")
        except VKVideoSearchError:
            return self.search(query="popular", count=count, sort_by="popularity", record_history=False)

    def build_telegram_cards(
        self,
        items: List[VideoResult],
        page: int = 1,
        page_size: int = 5,
        callback_prefix: str = "vk",
    ) -> Dict[str, Any]:
        page = max(page, 1)
        page_size = max(page_size, 1)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = items[start:end]
        total_pages = max(1, (len(items) + page_size - 1) // page_size)

        cards = []
        for item in page_items:
            caption = self._format_caption(item)
            keyboard = self._build_keyboard(item, callback_prefix)
            cards.append(
                {
                    "text": caption,
                    "preview_url": item.preview_url,
                    "watch_url": item.url,
                    "keyboard": keyboard,
                }
            )

        pagination = self._build_pagination(page, total_pages, callback_prefix)
        return {
            "cards": cards,
            "page": page,
            "total_pages": total_pages,
            "pagination": pagination,
        }

    def health_check(self, deep: bool = False) -> Dict[str, Any]:
        status = {
            "ok": True,
            "tokens_configured": bool(self._config.access_tokens),
            "cache": self._cache.stats(),
            "metrics": self._merged_metrics(),
        }
        if not status["tokens_configured"]:
            status["ok"] = False
            status["reason"] = "No VK tokens configured"
            return status
        if deep:
            try:
                response = self.search(query="health check", count=1, record_history=False)
                status["deep_check_items"] = len(response.items)
            except VKVideoSearchError as exc:
                status["ok"] = False
                status["reason"] = str(exc)
        return status

    def _search_sources(
        self,
        query: str,
        count: int,
        offset: int,
        filters: SearchFilters,
        sort_by: str,
        sources: SearchSources,
    ) -> Tuple[List[VideoResult], int]:
        results: List[VideoResult] = []
        total = 0

        if sources.include_global:
            fetch_count = min(max(count * 2, count), 200)
            params = {
                "q": query,
                "count": fetch_count,
                "offset": offset,
                "sort": self._vk_sort_param(sort_by),
                "extended": 1,
            }
            response = self._api.call("video.search", params)
            total = response.get("count", 0)
            items_raw = response.get("items", [])
            results.extend(self._normalize_video(v, source="vk") for v in items_raw)

        if sources.group_ids:
            for group_id in sources.group_ids:
                results.extend(self._search_group(query, group_id, count=count))

        if sources.playlists:
            for playlist in sources.playlists:
                results.extend(self._search_playlist(query, playlist, count=count))

        deduped = self._dedupe_results(results)
        filtered = self._apply_filters(deduped, filters)
        sorted_items = self._sort_results(filtered, sort_by)
        paged = sorted_items[offset : offset + count]

        if sources.group_ids or sources.playlists:
            total = len(sorted_items)

        return paged, total

    def _search_group(self, query: str, group_id: int, count: int) -> List[VideoResult]:
        params = {
            "owner_id": -abs(group_id),
            "count": min(count, 200),
            "extended": 1,
        }
        response = self._api.call("video.get", params)
        items_raw = response.get("items", [])
        results = [self._normalize_video(v, source="group") for v in items_raw]
        return [item for item in results if self._match_query(query, item)]

    def _search_playlist(self, query: str, playlist: PlaylistRef, count: int) -> List[VideoResult]:
        params = {
            "owner_id": playlist.owner_id,
            "album_id": playlist.album_id,
            "count": min(count, 200),
            "extended": 1,
        }
        response = self._api.call("video.get", params)
        items_raw = response.get("items", [])
        results = [self._normalize_video(v, source="playlist") for v in items_raw]
        return [item for item in results if self._match_query(query, item)]

    def _normalize_video(self, item: Dict[str, Any], source: str) -> VideoResult:
        owner_id = int(item.get("owner_id", 0))
        video_id = int(item.get("id", 0))
        access_key = item.get("access_key")
        url = f"https://vk.com/video{owner_id}_{video_id}"
        if access_key:
            url = f"{url}_{access_key}"
        tags = _normalize_tags(item.get("tags", []) if isinstance(item.get("tags"), list) else [])
        return VideoResult(
            id=video_id,
            owner_id=owner_id,
            title=item.get("title") or "Untitled",
            description=item.get("description") or "",
            duration=int(item.get("duration") or 0),
            views=int(item.get("views") or 0),
            date=int(item.get("date") or 0),
            url=url,
            preview_url=_select_preview(item.get("image", [])),
            tags=tags,
            access_key=access_key,
            source=source,
        )

    def _apply_filters(self, items: List[VideoResult], filters: SearchFilters) -> List[VideoResult]:
        tags = _normalize_tags(filters.tags)
        categories = _normalize_tags(filters.categories)
        filtered = []
        for item in items:
            if filters.min_duration is not None and item.duration < filters.min_duration:
                continue
            if filters.max_duration is not None and item.duration > filters.max_duration:
                continue
            if filters.min_views is not None and item.views < filters.min_views:
                continue
            if filters.max_views is not None and item.views > filters.max_views:
                continue
            if filters.min_date is not None and item.date < filters.min_date:
                continue
            if filters.max_date is not None and item.date > filters.max_date:
                continue
            if tags and not self._match_tags(item, tags, filters.match_all_tags):
                continue
            if categories and not self._match_tags(item, categories, filters.match_all_tags):
                continue
            filtered.append(item)
        return filtered

    def _match_tags(self, item: VideoResult, tags: Tuple[str, ...], match_all: bool) -> bool:
        haystack = f"{item.title} {item.description}".lower()
        if item.tags:
            haystack = f"{haystack} {' '.join(item.tags)}"
        matches = [tag for tag in tags if tag in haystack]
        if match_all:
            return len(matches) == len(tags)
        return bool(matches)

    def _match_query(self, query: str, item: VideoResult) -> bool:
        haystack = f"{item.title} {item.description}".lower()
        terms = _tokenize(query)
        return all(term in haystack for term in terms)

    def _sort_results(self, items: List[VideoResult], sort_by: str) -> List[VideoResult]:
        if sort_by == "popularity":
            return sorted(items, key=lambda item: item.views, reverse=True)
        if sort_by == "newest":
            return sorted(items, key=lambda item: item.date, reverse=True)
        if sort_by == "duration":
            return sorted(items, key=lambda item: item.duration, reverse=True)
        return items

    def _dedupe_results(self, items: List[VideoResult]) -> List[VideoResult]:
        seen = set()
        deduped = []
        for item in items:
            key = self._result_key(item)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _result_key(self, item: VideoResult) -> str:
        access_key = item.access_key or ""
        return f"{item.owner_id}_{item.id}_{access_key}"

    def _normalize_sort(self, sort_by: str) -> str:
        normalized = (sort_by or "relevance").lower()
        if normalized in {"relevance", "rel"}:
            return "relevance"
        if normalized in {"popular", "popularity", "views"}:
            return "popularity"
        if normalized in {"new", "newest", "date", "recent"}:
            return "newest"
        if normalized in {"duration", "length"}:
            return "duration"
        return "relevance"

    def _vk_sort_param(self, sort_by: str) -> int:
        if sort_by == "duration":
            return 1
        if sort_by == "newest":
            return 2
        if sort_by == "popularity":
            return 3
        return 0

    def _build_cache_key(
        self,
        query: str,
        count: int,
        offset: int,
        filters: SearchFilters,
        sort_by: str,
        sources: SearchSources,
    ) -> str:
        payload = {
            "query": query,
            "count": count,
            "offset": offset,
            "filters": filters.__dict__,
            "sort_by": sort_by,
            "sources": {
                "include_global": sources.include_global,
                "group_ids": sources.group_ids,
                "playlists": [(p.owner_id, p.album_id) for p in sources.playlists],
            },
        }
        return _hash_key(payload)

    def _extract_keywords(self, history: List[SearchRecord]) -> List[str]:
        tokens = Counter()
        for record in history:
            tokens.update(_tokenize(record.query))
        return [token for token, _ in tokens.most_common(10)]

    def _extract_keywords_from_text(self, text: str) -> List[str]:
        tokens = Counter(_tokenize(text))
        return [token for token, _ in tokens.most_common(10)]

    def _format_caption(self, item: VideoResult) -> str:
        duration = _format_duration(item.duration)
        return f"{item.title}\nViews: {item.views}\nDuration: {duration}"

    def _build_keyboard(self, item: VideoResult, prefix: str) -> Any:
        buttons = [
            {"text": "Watch", "url": item.url},
            {"text": "Save", "callback_data": f"{prefix}:save:{item.owner_id}_{item.id}"},
            {"text": "Share", "callback_data": f"{prefix}:share:{item.owner_id}_{item.id}"},
        ]
        return {"inline_keyboard": [buttons]}

    def _build_pagination(self, page: int, total_pages: int, prefix: str) -> Optional[Dict[str, Any]]:
        if total_pages <= 1:
            return None
        buttons = []
        if page > 1:
            buttons.append({"text": "Prev", "callback_data": f"{prefix}:page:{page - 1}"})
        if page < total_pages:
            buttons.append({"text": "Next", "callback_data": f"{prefix}:page:{page + 1}"})
        return {"inline_keyboard": [buttons]} if buttons else None

    def _merged_metrics(self) -> Dict[str, Any]:
        cache_stats = self._cache.stats()
        return {
            "api_calls": self._api.metrics.api_calls,
            "cache_hits": cache_stats["hits"],
            "cache_misses": cache_stats["misses"],
            "cache_size": cache_stats["size"],
            "rate_limit_waits": self._api.metrics.rate_limit_waits,
            "auth_errors": self._api.metrics.auth_errors,
            "api_errors": self._api.metrics.api_errors,
            "last_error": self._api.metrics.last_error,
            "last_call_time": self._api.metrics.last_call_time,
        }
