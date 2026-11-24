from __future__ import annotations

import asyncio
import json
import unittest
from email.message import Message
from http.client import IncompleteRead
from unittest.mock import patch
from urllib.error import URLError

from loom_app import main
from loom_app.schemas import SourceTestRequest


class FakeResponse:
    def __init__(self, body: bytes, *, fail_read: bool = False) -> None:
        self.body = body
        self.fail_read = fail_read
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        if self.fail_read:
            raise IncompleteRead(b"partial")
        return self.body


class FetchingTests(unittest.TestCase):
    def setUp(self) -> None:
        main.x_network_blocked_until = 0.0

    def test_source_headers_accept_unsaved_form_headers(self) -> None:
        headers = main.source_headers(
            {
                "headers": {
                    "Authorization": "Bearer test-token",
                    "Accept": "application/json",
                }
            }
        )

        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertIn("Mozilla/5.0", headers["User-Agent"])

    def test_fetch_bytes_retries_incomplete_reads(self) -> None:
        responses = [FakeResponse(b"", fail_read=True), FakeResponse(b'{"ok":true}')]
        with (
            patch.object(main, "urlopen", side_effect=responses) as mocked_urlopen,
            patch.object(main.time, "sleep"),
        ):
            body, _, status = main.fetch_bytes("https://example.com/data")

        self.assertEqual(body, b'{"ok":true}')
        self.assertEqual(status, 200)
        self.assertEqual(mocked_urlopen.call_count, 2)

    def test_source_test_passes_headers_and_parses_payload(self) -> None:
        request = SourceTestRequest(
            endpoint="https://example.com/feed.json",
            source_type="api",
            category="科技",
            headers={"X-Test-Key": "expected"},
        )

        def fake_fetch(source: dict[str, object]) -> tuple[str, str]:
            self.assertEqual(source["headers"], {"X-Test-Key": "expected"})
            return (
                json.dumps(
                    [
                        {
                            "title": "Test item",
                            "url": "https://example.com/article",
                            "published_at": "2026-08-20T00:00:00Z",
                        }
                    ]
                ),
                "application/json",
            )

        with patch.object(main, "fetch_source_payload", side_effect=fake_fetch):
            response = asyncio.run(main.test_source(request))

        self.assertTrue(response.ok)
        self.assertIn("1 条", response.message)

    def test_x_bearer_token_uses_official_api_v2(self) -> None:
        source = {
            "source_type": "x",
            "endpoint": "https://x.com/OpenAI",
            "method": "GET",
            "category": "AI",
            "headers": {"Authorization": "Bearer official-token"},
        }
        responses = [
            {"data": {"id": "123", "username": "OpenAI"}},
            {
                "data": [
                    {
                        "id": "456",
                        "text": "A new post",
                        "created_at": "2026-08-20T00:00:00Z",
                        "lang": "en",
                    }
                ]
            },
        ]

        with patch.object(main, "fetch_json_url", side_effect=responses) as mocked_fetch:
            payload = main.fetch_x_source_payload(source)

        decoded = json.loads(payload)
        self.assertEqual(decoded["loom_username"], "OpenAI")
        self.assertEqual(mocked_fetch.call_count, 2)
        first_headers = mocked_fetch.call_args_list[0].kwargs["headers"]
        self.assertEqual(first_headers["Authorization"], "Bearer official-token")

    def test_x_public_timeline_fallback_does_not_forward_credentials(self) -> None:
        source = {
            "source_type": "x",
            "endpoint": "https://x.com/OpenAI",
            "method": "GET",
            "category": "AI",
            "headers": {
                "Authorization": "Basic local-secret",
                "Cookie": "auth_token=local-secret",
                "X-Custom-Key": "local-secret",
            },
        }
        public_payload = {
            "code": 200,
            "results": [
                {
                    "id": "789",
                    "url": "https://x.com/OpenAI/status/789",
                    "text": "A public timeline post",
                    "created_at": "Mon Aug 24 21:03:46 +0000 2026",
                    "lang": "en",
                }
            ],
        }

        with (
            patch.object(
                main,
                "fetch_x_legacy_web_payload",
                side_effect=ValueError("new X page layout"),
            ),
            patch.object(main, "fetch_json_url", return_value=public_payload) as mocked_fetch,
        ):
            payload = main.fetch_x_source_payload(source)

        decoded = json.loads(payload)
        self.assertEqual(decoded["loom_username"], "OpenAI")
        self.assertEqual(len(decoded["results"]), 1)
        requested_url = mocked_fetch.call_args.args[0]
        self.assertEqual(
            requested_url,
            f"{main.X_PUBLIC_API_BASE_URL}/profile/OpenAI/statuses",
        )
        fallback_headers = mocked_fetch.call_args.kwargs["headers"]
        self.assertNotIn("Authorization", fallback_headers)
        self.assertNotIn("Cookie", fallback_headers)
        self.assertNotIn("X-Custom-Key", fallback_headers)

    def test_x_transport_failure_temporarily_skips_remaining_sources(self) -> None:
        source = {
            "source_type": "x",
            "endpoint": "https://x.com/OpenAI",
            "method": "GET",
            "category": "AI",
        }
        with patch.object(
            main,
            "_fetch_x_source_payload",
            side_effect=URLError("network unavailable"),
        ) as mocked_fetch:
            with self.assertRaisesRegex(ValueError, "无法连接 X"):
                main.fetch_x_source_payload(source)
            with self.assertRaisesRegex(ValueError, "后重试"):
                main.fetch_x_source_payload(source)

        self.assertEqual(mocked_fetch.call_count, 1)

    def test_parse_x_api_v2_payload(self) -> None:
        source = {
            "source_type": "x",
            "endpoint": "https://x.com/OpenAI",
            "category": "AI",
        }
        payload = json.dumps(
            {
                "loom_username": "OpenAI",
                "data": [
                    {
                        "id": "456",
                        "text": "A new post https://t.co/example",
                        "created_at": "2026-08-20T00:00:00Z",
                        "lang": "en",
                        "entities": {
                            "urls": [
                                {
                                    "url": "https://t.co/example",
                                    "expanded_url": "https://example.com/article",
                                }
                            ]
                        },
                    }
                ],
            }
        )

        entries = main.parse_x_entries(source, payload)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["original_url"], "https://x.com/OpenAI/status/456")
        self.assertNotIn("t.co", entries[0]["original_title"])

    def test_parse_x_public_timeline_payload(self) -> None:
        source = {
            "source_type": "x",
            "endpoint": "https://x.com/NASA",
            "category": "科技",
        }
        payload = json.dumps(
            {
                "code": 200,
                "results": [
                    {
                        "id": "2091971536781467729",
                        "url": "https://x.com/NASAKennedy/status/2091971536781467729",
                        "text": "Roman is packed up and ready to roll! https://go.nasa.gov/example",
                        "created_at": "Mon Aug 24 19:30:55 +0000 2026",
                        "lang": "en",
                        "author": {"screen_name": "NASAKennedy"},
                    }
                ],
            }
        )

        entries = main.parse_x_entries(source, payload)

        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0]["original_url"],
            "https://x.com/NASAKennedy/status/2091971536781467729",
        )
        self.assertEqual(entries[0]["published_at"], "2026-08-24T19:30:55+00:00")
        self.assertNotIn("go.nasa.gov", entries[0]["original_title"])

    def test_sync_lookback_matches_news_retention(self) -> None:
        self.assertEqual(main.SYNC_LOOKBACK_HOURS, main.NEWS_RETENTION_HOURS)

    def test_parse_x_semantic_profile_html(self) -> None:
        profile_html = """
        <article data-tweet-id="789" itemprop="hasPart">
          <meta content="2026-08-20T00:00:00.000Z" itemprop="datePublished" />
          <meta content="A semantic profile post https://t.co/example" itemprop="text" />
          <meta content="https://x.com/OpenAI/status/789" itemprop="url" />
          <div itemprop="author">
            <meta content="OpenAI" itemprop="alternateName" />
          </div>
        </article>
        """

        payload = main.extract_x_profile_payload(profile_html, "fallback")
        entries = main.parse_x_entries(
            {
                "source_type": "x",
                "endpoint": "https://x.com/OpenAI",
                "category": "AI",
            },
            payload,
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["original_url"], "https://x.com/OpenAI/status/789")
        self.assertEqual(entries[0]["published_at"], "2026-08-20T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
