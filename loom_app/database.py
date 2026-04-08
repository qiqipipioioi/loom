from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "loom.db"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    return connection


@contextmanager
def cursor(connection: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
    db_cursor = connection.cursor()
    try:
        yield db_cursor
        connection.commit()
    finally:
        db_cursor.close()


def initialize_database(connection: sqlite3.Connection) -> None:
    with cursor(connection) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL DEFAULT 'GET',
                category TEXT NOT NULL DEFAULT '其他',
                poll_interval_minutes INTEGER NOT NULL DEFAULT 15,
                enabled INTEGER NOT NULL DEFAULT 1,
                health_status TEXT NOT NULL DEFAULT 'idle',
                last_success_at TEXT,
                last_error_at TEXT,
                last_error_message TEXT,
                headers_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                original_title TEXT NOT NULL,
                translated_title TEXT,
                summary_zh TEXT,
                original_content TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT 'zh',
                category TEXT NOT NULL DEFAULT '其他',
                original_url TEXT NOT NULL,
                published_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES sources(source_id)
            );

            CREATE TABLE IF NOT EXISTS clusters (
                cluster_id TEXT PRIMARY KEY,
                canonical_title TEXT NOT NULL,
                canonical_summary_zh TEXT NOT NULL,
                primary_category TEXT NOT NULL,
                importance_score REAL NOT NULL DEFAULT 0,
                heat_score REAL NOT NULL DEFAULT 0,
                source_count INTEGER NOT NULL DEFAULT 0,
                message_count INTEGER NOT NULL DEFAULT 0,
                latest_published_at TEXT NOT NULL,
                original_link TEXT NOT NULL,
                ai_status TEXT NOT NULL DEFAULT 'ready',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cluster_message_map (
                cluster_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                PRIMARY KEY (cluster_id, message_id),
                FOREIGN KEY (cluster_id) REFERENCES clusters(cluster_id),
                FOREIGN KEY (message_id) REFERENCES messages(message_id)
            );

            CREATE TABLE IF NOT EXISTS system_state (
                state_key TEXT PRIMARY KEY,
                state_value TEXT NOT NULL
            );
            """
        )
        message_columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "original_content" not in message_columns:
            db.execute(
                "ALTER TABLE messages ADD COLUMN original_content TEXT NOT NULL DEFAULT ''"
            )
            db.execute(
                "UPDATE messages SET original_content = COALESCE(summary_zh, '') "
                "WHERE TRIM(COALESCE(original_content, '')) = ''"
            )
    seed_demo_data(connection)


def seed_demo_data(connection: sqlite3.Connection) -> None:
    with cursor(connection) as db:
        source_count = db.execute("SELECT COUNT(*) AS count FROM sources").fetchone()["count"]
        if source_count:
            return

        now = utc_now_iso()
        sources = [
            {
                "source_id": str(uuid4()),
                "name": "OpenAI Blog",
                "source_type": "feed",
                "endpoint": "https://openai.com/news/rss.xml",
                "method": "GET",
                "category": "AI",
                "poll_interval_minutes": 15,
                "enabled": 1,
                "health_status": "healthy",
                "last_success_at": now,
                "last_error_at": None,
                "last_error_message": None,
                "headers_json": "{}",
            },
            {
                "source_id": str(uuid4()),
                "name": "TechCrunch AI",
                "source_type": "api",
                "endpoint": "https://techcrunch.com/tag/artificial-intelligence/feed/",
                "method": "GET",
                "category": "科技",
                "poll_interval_minutes": 20,
                "enabled": 1,
                "health_status": "warning",
                "last_success_at": now,
                "last_error_at": None,
                "last_error_message": None,
                "headers_json": "{}",
            },
            {
                "source_id": str(uuid4()),
                "name": "新华社快讯",
                "source_type": "api",
                "endpoint": "https://example.local/xinhua/breaking",
                "method": "GET",
                "category": "时政",
                "poll_interval_minutes": 10,
                "enabled": 1,
                "health_status": "healthy",
                "last_success_at": now,
                "last_error_at": None,
                "last_error_message": None,
                "headers_json": "{}",
            },
        ]

        db.executemany(
            """
            INSERT INTO sources (
                source_id, name, source_type, endpoint, method, category,
                poll_interval_minutes, enabled, health_status, last_success_at,
                last_error_at, last_error_message, headers_json, created_at, updated_at
            ) VALUES (
                :source_id, :name, :source_type, :endpoint, :method, :category,
                :poll_interval_minutes, :enabled, :health_status, :last_success_at,
                :last_error_at, :last_error_message, :headers_json, :created_at, :updated_at
            )
            """,
            [{**item, "created_at": now, "updated_at": now} for item in sources],
        )

        cluster_specs = [
            {
                "canonical_title": "OpenAI 发布新模型路线图",
                "canonical_summary_zh": (
                    "多家来源提到 OpenAI 正在为下一代模型和代理能力做铺垫，"
                    "市场关注重点集中在推理能力与产品化节奏。"
                ),
                "primary_category": "AI",
                "importance_score": 92,
                "heat_score": 78,
                "source_count": 2,
                "message_count": 2,
                "minutes_ago": 7,
                "original_link": "https://openai.com/news/",
                "ai_status": "ready",
                "messages": [
                    {
                        "source_name": "OpenAI Blog",
                        "original_title": "Planning the next phase of frontier models",
                        "translated_title": "OpenAI 规划下一阶段前沿模型",
                        "summary_zh": "官方博客说明后续模型节奏、工具能力与部署方向。",
                        "original_content": (
                            "OpenAI 在官方博客中说明，下一阶段工作会围绕更强的推理能力、"
                            "更稳定的工具使用体验，以及更清晰的模型发布节奏展开。"
                        ),
                        "language": "en",
                        "category": "AI",
                        "original_url": "https://openai.com/news/planning-the-next-phase",
                    },
                    {
                        "source_name": "TechCrunch AI",
                        "original_title": "OpenAI hints at a new model roadmap",
                        "translated_title": "OpenAI 暗示新模型路线图",
                        "summary_zh": "媒体报道将官方动作解读为下一代模型发布前的预热信号。",
                        "original_content": (
                            "报道称，OpenAI 最近的对外表述被市场视作新模型路线图的前置信号，"
                            "外界关注重点集中在发布时间窗口、产品化节奏与能力分层。"
                        ),
                        "language": "en",
                        "category": "科技",
                        "original_url": "https://techcrunch.com/example/openai-roadmap",
                    },
                ],
            },
            {
                "canonical_title": "全球主要市场继续评估美联储表态",
                "canonical_summary_zh": (
                    "政策表述偏谨慎，市场分歧集中在未来降息节奏，"
                    "外汇与风险资产波动同步放大。"
                ),
                "primary_category": "宏观",
                "importance_score": 81,
                "heat_score": 69,
                "source_count": 2,
                "message_count": 2,
                "minutes_ago": 18,
                "original_link": "https://example.local/macro/fed",
                "ai_status": "ready",
                "messages": [
                    {
                        "source_name": "新华社快讯",
                        "original_title": "市场持续关注美联储最新政策信号",
                        "translated_title": "市场持续关注美联储最新政策信号",
                        "summary_zh": "快讯聚焦政策表态对市场预期的影响。",
                        "original_content": (
                            "多类资产继续围绕美联储最新表态进行重新定价，"
                            "市场分歧主要集中在未来降息节奏与通胀回落速度。"
                        ),
                        "language": "zh",
                        "category": "时政",
                        "original_url": "https://example.local/xinhua/fed-signal",
                    },
                    {
                        "source_name": "TechCrunch AI",
                        "original_title": "Investors weigh the latest Fed tone across risk assets",
                        "translated_title": "投资者重新评估美联储基调对风险资产的影响",
                        "summary_zh": "海外观察指出市场对未来利率路径仍缺乏一致预期。",
                        "original_content": (
                            "Investors are reassessing the Federal Reserve's latest tone across "
                            "equities, currencies and rates as disagreement persists over the "
                            "timing and depth of future cuts."
                        ),
                        "language": "en",
                        "category": "宏观",
                        "original_url": "https://example.local/markets/fed-tone",
                    },
                ],
            },
        ]

        source_map = {item["name"]: item["source_id"] for item in sources}
        for cluster_spec in cluster_specs:
            published_at = (
                datetime.now(UTC) - timedelta(minutes=cluster_spec["minutes_ago"])
            ).isoformat()
            cluster_id = str(uuid4())
            db.execute(
                """
                INSERT INTO clusters (
                    cluster_id, canonical_title, canonical_summary_zh, primary_category,
                    importance_score, heat_score, source_count, message_count,
                    latest_published_at, original_link, ai_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cluster_id,
                    cluster_spec["canonical_title"],
                    cluster_spec["canonical_summary_zh"],
                    cluster_spec["primary_category"],
                    cluster_spec["importance_score"],
                    cluster_spec["heat_score"],
                    cluster_spec["source_count"],
                    cluster_spec["message_count"],
                    published_at,
                    cluster_spec["original_link"],
                    cluster_spec["ai_status"],
                    now,
                ),
            )
            for message_spec in cluster_spec["messages"]:
                message_id = str(uuid4())
                db.execute(
                    """
                    INSERT INTO messages (
                        message_id, source_id, original_title, translated_title, summary_zh,
                        original_content, language, category, original_url, published_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        source_map[message_spec["source_name"]],
                        message_spec["original_title"],
                        message_spec["translated_title"],
                        message_spec["summary_zh"],
                        message_spec.get("original_content", message_spec["summary_zh"]),
                        message_spec["language"],
                        message_spec["category"],
                        message_spec["original_url"],
                        published_at,
                        now,
                    ),
                )
                db.execute(
                    "INSERT INTO cluster_message_map (cluster_id, message_id) VALUES (?, ?)",
                    (cluster_id, message_id),
                )

        set_system_state(
            db,
            {
                "network_status": "online",
                "ai_status": "disconnected",
                "last_sync_at": now,
                "last_cleanup_at": now,
            },
        )


def set_system_state(db: sqlite3.Cursor, updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        db.execute(
            """
            INSERT INTO system_state (state_key, state_value)
            VALUES (?, ?)
            ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value
            """,
            (key, payload),
        )
