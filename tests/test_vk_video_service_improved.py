import os
import time
import unittest

from vk_video_service_improved import (
    Metrics,
    SearchFilters,
    VKVideoSearchConfig,
    VKVideoSearchService,
    VKInvalidQueryError,
    VideoResult,
)


SAMPLE_ITEMS = [
    {
        "id": 1,
        "owner_id": 10,
        "title": "Test video one",
        "description": "Sample description one",
        "duration": 120,
        "views": 100,
        "date": 1700000001,
        "image": [{"url": "http://example.com/1.jpg", "width": 100, "height": 100}],
    },
    {
        "id": 2,
        "owner_id": 11,
        "title": "Another sample video",
        "description": "Description two",
        "duration": 300,
        "views": 500,
        "date": 1700001000,
        "image": [{"url": "http://example.com/2.jpg", "width": 200, "height": 200}],
    },
]


class FakeApiClient:
    def __init__(self) -> None:
        self.call_count = 0
        self.metrics = Metrics()

    def call(self, method, params):  # noqa: ANN001
        self.call_count += 1
        if method == "video.search":
            return {"count": len(SAMPLE_ITEMS), "items": SAMPLE_ITEMS}
        if method == "video.getPopular":
            return {"count": len(SAMPLE_ITEMS), "items": SAMPLE_ITEMS}
        return {"count": 0, "items": []}


class VKVideoSearchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        config = VKVideoSearchConfig(access_tokens=["test"])
        self.service = VKVideoSearchService(config=config, api_client=FakeApiClient())

    def test_sanitize_query(self):
        with self.assertRaises(VKInvalidQueryError):
            self.service.search("")
        response = self.service.search(" test query ", count=1, use_cache=False)
        self.assertEqual(response.items[0].title, "Test video one")

    def test_filtering_by_duration_and_views(self):
        filters = SearchFilters(min_duration=200, min_views=200)
        response = self.service.search("video", filters=filters, use_cache=False)
        self.assertEqual(len(response.items), 1)
        self.assertEqual(response.items[0].id, 2)

    def test_sorting_by_popularity(self):
        response = self.service.search("video", sort_by="popularity", use_cache=False)
        self.assertEqual(response.items[0].id, 2)

    def test_cache_hit(self):
        self.service.search("video", count=2, use_cache=True)
        self.service.search("video", count=2, use_cache=True)
        self.assertEqual(self.service._api.call_count, 1)

    def test_telegram_cards_pagination(self):
        response = self.service.search("video", count=2, use_cache=False)
        cards = self.service.build_telegram_cards(response.items, page=1, page_size=1)
        self.assertEqual(cards["page"], 1)
        self.assertEqual(cards["total_pages"], 2)
        self.assertEqual(len(cards["cards"]), 1)


class VKVideoSearchIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("VK_ACCESS_TOKEN") or os.getenv("VK_ACCESS_TOKENS"),
        "Requires VK access token",
    )
    def test_real_search(self):
        service = VKVideoSearchService()
        response = service.search("cats", count=1, use_cache=False, record_history=False)
        self.assertIsNone(response.error)


class VKVideoSearchPerformanceTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("RUN_PERF_TESTS"), "Performance tests are disabled")
    def test_cache_is_faster(self):
        config = VKVideoSearchConfig(access_tokens=["test"])
        service = VKVideoSearchService(config=config, api_client=FakeApiClient())
        start = time.perf_counter()
        service.search("video", count=2, use_cache=True)
        first_duration = time.perf_counter() - start
        start = time.perf_counter()
        service.search("video", count=2, use_cache=True)
        second_duration = time.perf_counter() - start
        self.assertLessEqual(second_duration, first_duration)


if __name__ == "__main__":
    unittest.main()
