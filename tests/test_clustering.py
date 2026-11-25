from __future__ import annotations

import asyncio
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from loom_app import main
from loom_app.database import initialize_database

CLASHREPORT = (
    "Trump says he is open to pardoning Nvidia CEO Jensen Huang but does not believe it will "
    "be necessary. Speaking to the New York Post, Trump urged Huang to testify before Congress,…",
    "特朗普称可考虑赦免英伟达CEO黄仁勋，但认为无需如此",
)
LEADING_REPORT = (
    "President Trump say he would be open to pardoning Nvidia CEO Jensen Huang if necessary, "
    "per NYP.",
    "特朗普称必要时愿赦免英伟达CEO黄仁勋",
)
BLOOMBERG = (
    "President Trump said a pardon for the reinstated Nvidia chief Jensen Huang was “always an "
    "option,” quickly adding that he didn’t think such an extraordinary act would be necessary…",
    "特朗普称可考虑赦免复职的英伟达CEO黄仁勋",
)
UNRELATED = (
    "Trump joked about Nvidia CEO Jensen Huang’s alleged affairs, saying he was more interested "
    "in the chip business than his sex life.",
    "特朗普调侃英伟达CEO黄仁勋的传闻风流韵事",
)


class ClusteringTests(unittest.TestCase):
    def setUp(self) -> None:
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        with (
            patch("loom_app.database.seed_demo_data"),
            patch("loom_app.database.install_market_feeds"),
        ):
            initialize_database(db)
        self.enterContext(patch.object(main, "connection", db))
        self.enterContext(patch.object(main, "apply_ai_enrichment", side_effect=self.enrich))
        self.now = datetime.now(UTC)

    @staticmethod
    def enrich(_source, message):
        return message, None

    def persist(self, source: str, title: tuple[str, str], minutes_ago: int) -> None:
        published_at = (self.now - timedelta(minutes=minutes_ago)).isoformat()
        url = f"https://example.com/{source}/{minutes_ago}"
        with main.connection:
            main.connection.execute(
                "INSERT OR IGNORE INTO sources "
                "(source_id, name, source_type, endpoint, created_at, updated_at) "
                "VALUES (?, ?, 'feed', ?, ?, ?)",
                (source, source, url, published_at, published_at),
            )
        result = main.persist_messages(
            {"source_id": source},
            [
                {
                    "original_title": title[0],
                    "translated_title": title[1],
                    "summary_zh": title[1],
                    "original_content": title[0],
                    "language": "en",
                    "category": "时政",
                    "original_url": url,
                    "published_at": published_at,
                }
            ],
        )
        self.assertEqual(result[0], 1)

    def timeline(self):
        return asyncio.run(main.get_timeline(sort_by="latest", limit=20, offset=0))

    def assert_grouped(self) -> None:
        timeline = self.timeline()
        self.assertEqual(timeline.total, 2)
        cluster, unrelated = timeline.clusters
        self.assertEqual((cluster.source_count, cluster.message_count), (3, 3))
        self.assertEqual(cluster.canonical_title, BLOOMBERG[1])
        self.assertEqual(cluster.canonical_original_title, BLOOMBERG[0])
        messages = asyncio.run(main.get_cluster_messages(cluster.cluster_id))
        self.assertEqual(
            [message.source_name for message in messages],
            ["bloomberg", "leading report", "clashreport"],
        )
        self.assertEqual(
            [message.original_content for message in messages],
            [
                BLOOMBERG[0],
                LEADING_REPORT[0],
                CLASHREPORT[0],
            ],
        )
        self.assertEqual(unrelated.canonical_title, UNRELATED[1])
        self.assertEqual(unrelated.message_count, 1)

    def test_late_report_joins_similar_clusters_and_keeps_latest_headline(self) -> None:
        self.persist("bloomberg", BLOOMBERG, 5)
        self.persist("leading report", LEADING_REPORT, 110)
        self.assertEqual(self.timeline().total, 2)
        self.persist("clashreport", CLASHREPORT, 165)
        self.persist("clashreport", UNRELATED, 164)
        self.assert_grouped()

        cluster_id = self.timeline().clusters[0].cluster_id
        cutoff = self.now + timedelta(hours=24, minutes=-164, seconds=-30)
        with main.connection, main.cursor(main.connection) as db:
            self.assertEqual(main.cleanup_expired_news(db, cutoff), 1)
        cluster, unrelated = self.timeline().clusters
        self.assertEqual(cluster.cluster_id, cluster_id)
        self.assertEqual((cluster.source_count, cluster.message_count), (2, 2))
        self.assertEqual(unrelated.message_count, 1)

    def test_startup_reclusters_retained_reports_even_without_expired_news(self) -> None:
        with patch.object(main, "same_story", return_value=False):
            self.persist("clashreport", CLASHREPORT, 165)
            self.persist("clashreport", UNRELATED, 164)
            self.persist("leading report", LEADING_REPORT, 110)
            self.persist("bloomberg", BLOOMBERG, 5)
        self.assertEqual(self.timeline().total, 4)

        async def startup():
            async with main.lifespan(main.app):
                pass

        with (
            patch("loom_app.database.seed_demo_data"),
            patch("loom_app.database.install_market_feeds"),
        ):
            asyncio.run(startup())
        self.assert_grouped()

    def test_similar_earnings_headlines_for_different_companies_stay_separate(self) -> None:
        self.persist(
            "workday",
            (
                "Workday Announces Fiscal 2027 Third Quarter Financial Results",
                "Workday公布2027财年第三季度财务业绩",
            ),
            80,
        )
        self.persist(
            "dell",
            (
                "Dell Technologies Delivers Third Quarter Fiscal 2027 Financial Results",
                "戴尔科技公布2027财年第三季度财务业绩",
            ),
            40,
        )
        self.assertEqual(self.timeline().total, 2)
