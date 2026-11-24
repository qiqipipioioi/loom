from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.client import IncompleteRead, RemoteDisconnected
from queue import Empty, Queue
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from loom_app.database import (
    cursor,
    initialize_database,
    make_connection,
    set_system_state,
    utc_now_iso,
)
from loom_app.schemas import (
    AIConfigResponse,
    AIConfigUpdate,
    AIConnectionTestResponse,
    ClusterMessageOut,
    SourceCreate,
    SourceOut,
    SourceStatusUpdate,
    SourceTestRequest,
    SourceTestResponse,
    SourceUpdate,
    SyncRequest,
    SystemStatusResponse,
    TimelineResponse,
    XBulkImportRequest,
    XBulkImportResponse,
)

SOURCE_SELECT = """
SELECT source_id, name, source_type, endpoint, method, category, poll_interval_minutes,
       enabled, health_status, last_success_at, last_error_at, last_error_message,
       headers_json, created_at, updated_at
FROM sources
"""
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
X_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
X_PUBLIC_API_BASE_URL = os.getenv(
    "LOOM_X_PUBLIC_API_BASE_URL", "https://api.fxtwitter.com/2"
).strip().rstrip("/")
DEFAULT_ACCEPT_HEADER = (
    "application/json, application/xml, text/xml, "
    "application/rss+xml, text/html;q=0.9"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
DEFAULT_AI_BASE_URL = "http://127.0.0.1:1234"
DEFAULT_AI_CATEGORIES = ["时政", "AI", "科技", "宏观", "数字货币", "其他"]
FETCH_RETRY_ATTEMPTS = 3
X_NETWORK_COOLDOWN_SECONDS = 60
sync_lock = asyncio.Lock()
x_client_cache: dict[str, object] = {}
x_client_lock = Lock()
x_network_blocked_until = 0.0
sync_scheduler_task: asyncio.Task[None] | None = None
SYNC_TICK_SECONDS = 30
NEWS_RETENTION_HOURS = 24
SYNC_LOOKBACK_HOURS = NEWS_RETENTION_HOURS
MAX_X_MESSAGES_PER_SOURCE = 10
logger = logging.getLogger(__name__)
TRANSIENT_NETWORK_ERRORS = (
    IncompleteRead,
    RemoteDisconnected,
    TimeoutError,
    ConnectionError,
    URLError,
)
SOURCE_SYNC_ERRORS = (
    ET.ParseError,
    ValueError,
    HTTPError,
    URLError,
    json.JSONDecodeError,
    IncompleteRead,
    RemoteDisconnected,
    TimeoutError,
    ConnectionError,
)


class RealtimeHub:
    def __init__(self) -> None:
        self._subscribers: set[Queue[str]] = set()
        self._lock = Lock()

    def subscribe(self) -> Queue[str]:
        queue: Queue[str] = Queue()
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def broadcast(self, event: dict[str, object]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        with self._lock:
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            queue.put(payload)


hub = RealtimeHub()
connection = make_connection()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global sync_scheduler_task
    initialize_database(connection)
    with cursor(connection) as db:
        cleanup_expired_news(db)
    sync_scheduler_task = asyncio.create_task(sync_scheduler_loop())
    try:
        yield
    finally:
        if sync_scheduler_task is not None:
            sync_scheduler_task.cancel()
            try:
                await sync_scheduler_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("同步调度任务异常退出")
            sync_scheduler_task = None


app = FastAPI(title="Loom", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="loom_app/static"), name="static")


def row_to_source(row: object) -> SourceOut:
    mapping = dict(row)
    if str(mapping.get("source_type") or "").lower() == "rss":
        mapping["source_type"] = "feed"
    mapping["enabled"] = bool(mapping["enabled"])
    mapping["headers"] = json.loads(mapping.pop("headers_json"))
    return SourceOut.model_validate(mapping)


def list_sources() -> list[SourceOut]:
    rows = connection.execute(f"{SOURCE_SELECT} ORDER BY created_at ASC, name ASC").fetchall()
    return [row_to_source(row) for row in rows]


def get_source_row(source_id: str) -> dict[str, object] | None:
    row = connection.execute(
        f"{SOURCE_SELECT} WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


def update_sync_state() -> None:
    with cursor(connection) as db:
        set_system_state(db, {"last_sync_at": utc_now_iso()})


def emit(event_type: str) -> None:
    hub.broadcast({"type": event_type, "sent_at": utc_now_iso()})


def get_system_state_map() -> dict[str, str]:
    rows = connection.execute("SELECT state_key, state_value FROM system_state").fetchall()
    return {row["state_key"]: row["state_value"] for row in rows}


def decode_state_json(value: str | None, default: object) -> object:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def decode_state_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    if value in {"true", "1", "True"}:
        return True
    if value in {"false", "0", "False"}:
        return False
    return default


def normalize_lmstudio_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    path = parsed.path.rstrip("/")
    for suffix in ("/v1/chat/completions", "/v1/models", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def normalize_ai_categories(categories: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in categories:
        value = normalize_whitespace(str(item)).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(truncate_text(value, 20))
    return normalized or DEFAULT_AI_CATEGORIES.copy()


def get_ai_config() -> AIConfigResponse:
    state = get_system_state_map()
    base_url = normalize_lmstudio_base_url(state.get("ai_base_url", DEFAULT_AI_BASE_URL))
    model = state.get("ai_model", "")
    categories = decode_state_json(state.get("ai_categories"), DEFAULT_AI_CATEGORIES.copy())
    category_list = categories if isinstance(categories, list) else DEFAULT_AI_CATEGORIES.copy()
    return AIConfigResponse(
        base_url=base_url,
        model=model,
        has_api_key=bool(state.get("ai_api_key")),
        translation_enabled=decode_state_bool(state.get("ai_translation_enabled"), True),
        classification_enabled=decode_state_bool(state.get("ai_classification_enabled"), True),
        categories=normalize_ai_categories([str(item) for item in category_list]),
    )


def ai_is_configured(config: AIConfigResponse) -> bool:
    return bool(config.base_url and config.model)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def news_retention_cutoff(reference_time: datetime | None = None) -> datetime:
    return (reference_time or datetime.now(UTC)) - timedelta(hours=NEWS_RETENTION_HOURS)


def sync_lookback_cutoff(reference_time: datetime | None = None) -> datetime:
    return (reference_time or datetime.now(UTC)) - timedelta(hours=SYNC_LOOKBACK_HOURS)


def is_within_news_retention(published_at: str, reference_time: datetime | None = None) -> bool:
    published_time = parse_iso_datetime(published_at)
    if published_time is None:
        return True
    return published_time >= news_retention_cutoff(reference_time)


def is_within_sync_lookback(published_at: str, reference_time: datetime | None = None) -> bool:
    published_time = parse_iso_datetime(published_at)
    if published_time is None:
        return True
    return published_time >= sync_lookback_cutoff(reference_time)


def cleanup_expired_news(db, reference_time: datetime | None = None) -> int:
    cutoff_iso = news_retention_cutoff(reference_time).isoformat()
    expired_rows = db.execute(
        "SELECT message_id FROM messages WHERE published_at < ?",
        (cutoff_iso,),
    ).fetchall()
    if not expired_rows:
        return 0
    db.execute(
        "DELETE FROM cluster_message_map WHERE message_id IN "
        "(SELECT message_id FROM messages WHERE published_at < ?)",
        (cutoff_iso,),
    )
    db.execute("DELETE FROM messages WHERE published_at < ?", (cutoff_iso,))
    rebuild_clusters(db)
    set_system_state(db, {"last_cleanup_at": utc_now_iso()})
    return len(expired_rows)


def source_last_polled_at(source: dict[str, object]) -> datetime | None:
    candidates = (
        parse_iso_datetime(str(source["last_success_at"])) if source["last_success_at"] else None,
        parse_iso_datetime(str(source["last_error_at"])) if source["last_error_at"] else None,
    )
    timestamps = [item for item in candidates if item is not None]
    if not timestamps:
        return None
    return max(timestamps)


def list_due_source_rows(reference_time: datetime | None = None) -> list[dict[str, object]]:
    now = reference_time or datetime.now(UTC)
    rows = connection.execute(
        f"{SOURCE_SELECT} WHERE enabled = 1 ORDER BY created_at ASC"
    ).fetchall()
    due_sources: list[dict[str, object]] = []
    for row in rows:
        source = dict(row)
        last_polled_at = source_last_polled_at(source)
        if last_polled_at is None:
            due_sources.append(source)
            continue
        poll_interval = max(1, int(source["poll_interval_minutes"]))
        if last_polled_at + timedelta(minutes=poll_interval) <= now:
            due_sources.append(source)
    return due_sources


def source_headers(source: dict[str, object]) -> dict[str, str]:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": DEFAULT_ACCEPT_HEADER,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "close",
    }
    custom_headers = source.get("headers")
    if not isinstance(custom_headers, dict):
        raw_headers = source.get("headers_json") or "{}"
        try:
            custom_headers = json.loads(str(raw_headers))
        except json.JSONDecodeError:
            custom_headers = {}
    if isinstance(custom_headers, dict):
        headers.update({str(key): str(value) for key, value in custom_headers.items()})
    return headers


def header_value(headers: dict[str, str], name: str) -> str:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    return ""


def fetch_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 20,
    attempts: int = FETCH_RETRY_ATTEMPTS,
) -> tuple[bytes, object, int]:
    last_error: BaseException | None = None
    for attempt in range(max(1, attempts)):
        request = Request(url, data=data, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", response.getcode()))
                return response.read(), response.headers, status
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                raise
        except TRANSIENT_NETWORK_ERRORS as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
        time.sleep(0.2 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("请求未执行")


def fetch_json_url(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 20,
    attempts: int = FETCH_RETRY_ATTEMPTS,
) -> dict[str, object]:
    payload, _, _ = fetch_bytes(
        url,
        method=method,
        headers=headers,
        data=data,
        timeout=timeout,
        attempts=attempts,
    )
    decoded = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(decoded, dict):
        raise ValueError("接口未返回 JSON 对象")
    return decoded


def normalize_x_handle(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("X 账号不能为空")
    parsed = urlsplit(candidate)
    if parsed.netloc.lower() in X_HOSTS:
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not segments:
            raise ValueError("X 来源地址缺少账号名")
        candidate = segments[0]
    else:
        candidate = candidate.split("/", 1)[0]
    normalized = candidate.lstrip("@")
    if not X_HANDLE_PATTERN.fullmatch(normalized):
        raise ValueError(f"无效的 X 账号：{value}")
    return normalized


def resolve_source_endpoint(source_type: str, endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if source_type != "x":
        return endpoint
    if parsed.netloc.lower() in X_HOSTS:
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not segments:
            return endpoint
        handle = segments[0].lstrip("@")
        if not handle:
            return endpoint
        return f"https://x.com/{quote(handle)}"
    return endpoint


def fetch_source_payload(source: dict[str, object]) -> tuple[str, str]:
    if str(source["source_type"]) == "x":
        return fetch_x_source_payload(source), "application/json"
    endpoint = resolve_source_endpoint(str(source["source_type"]), str(source["endpoint"]))
    method = "GET" if str(source["source_type"]) == "x" else str(source["method"]).upper()
    body, response_headers, _ = fetch_bytes(
        endpoint,
        method=method,
        headers=source_headers(source),
        timeout=12,
    )
    charset = response_headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace"), response_headers.get("Content-Type", "")


def extract_x_handle(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.netloc.lower() not in X_HOSTS:
        raise ValueError("X 来源需提供 x.com 或 twitter.com 的公开账号地址")
    handle = normalize_x_handle(endpoint)
    if handle.lower() in {"home", "explore", "search", "i"}:
        raise ValueError("X 来源地址格式无效，请填写账号主页地址")
    return handle


def parse_bulk_x_handles(accounts_text: str) -> list[str]:
    candidates = re.split(r"[\s,，;；]+", accounts_text.strip())
    handles: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        handle = normalize_x_handle(candidate)
        lowered = handle.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        handles.append(handle)
    if not handles:
        raise ValueError("请至少输入一个 X 账号或主页链接")
    return handles


def create_source_record(payload: SourceCreate) -> SourceOut:
    now = utc_now_iso()
    source_id = str(uuid4())
    with cursor(connection) as db:
        db.execute(
            """
            INSERT INTO sources (
                source_id, name, source_type, endpoint, method, category, poll_interval_minutes,
                enabled, health_status, last_success_at, last_error_at, last_error_message,
                headers_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                payload.name,
                payload.source_type,
                str(payload.endpoint),
                payload.method.upper(),
                payload.category,
                payload.poll_interval_minutes,
                int(payload.enabled),
                "idle",
                None,
                None,
                None,
                json.dumps(payload.headers, ensure_ascii=False),
                now,
                now,
            ),
        )
    row = connection.execute(
        f"{SOURCE_SELECT} WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    return row_to_source(row)


def fetch_text(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    timeout: int = 20,
    attempts: int = FETCH_RETRY_ATTEMPTS,
) -> tuple[str, object]:
    body, response_headers, _ = fetch_bytes(
        url,
        headers=headers,
        timeout=timeout,
        attempts=attempts,
    )
    charset = response_headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace"), response_headers


class XProfileArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._article_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "article":
            if self._current is not None:
                self._article_depth += 1
                return
            tweet_id = attributes.get("data-tweet-id", "").strip()
            if tweet_id:
                self._current = {"id": tweet_id}
                self._article_depth = 1
            return
        if tag != "meta" or self._current is None or self._article_depth != 1:
            return
        item_property = attributes.get("itemprop", "")
        content = attributes.get("content", "").strip()
        field_map = {
            "datePublished": "created_at",
            "text": "text",
            "url": "url",
            "alternateName": "username",
        }
        field_name = field_map.get(item_property)
        if field_name and content and field_name not in self._current:
            self._current[field_name] = content

    def handle_endtag(self, tag: str) -> None:
        if tag != "article" or self._current is None:
            return
        self._article_depth -= 1
        if self._article_depth > 0:
            return
        if self._current.get("id") and self._current.get("text"):
            self.posts.append(self._current)
        self._current = None
        self._article_depth = 0


def extract_x_profile_payload(profile_html: str, fallback_handle: str) -> str:
    parser = XProfileArticleParser()
    parser.feed(profile_html)
    if not parser.posts:
        return ""
    posts: list[dict[str, object]] = []
    username = fallback_handle
    seen_ids: set[str] = set()
    for item in parser.posts:
        post_id = item["id"]
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        username = item.get("username") or username
        posts.append(
            {
                "id": post_id,
                "text": item["text"],
                "created_at": item.get("created_at", ""),
            }
        )
        if len(posts) >= MAX_X_MESSAGES_PER_SOURCE:
            break
    return json.dumps(
        {"data": posts, "loom_username": username},
        ensure_ascii=False,
    )


def get_x_client_bundle(
    profile_html: str,
    request_headers: dict[str, str] | None = None,
    profile_url: str = "https://x.com/",
) -> dict[str, str]:
    script_match = re.search(
        r'https://abs\.twimg\.com/responsive-web/client-web/main\.[^"\']+\.js',
        profile_html,
    )
    if not script_match:
        raise ValueError(
            "X 页面没有可解析的公开帖子；请确认账号公开，或配置官方 X API Bearer Token"
        )
    main_js_url = script_match.group(0)
    with x_client_lock:
        cached = x_client_cache.get(main_js_url)
        if isinstance(cached, dict):
            return cached
    script_headers = dict(request_headers or {})
    script_headers.update({"Accept": "*/*", "Referer": profile_url})
    main_js, _ = fetch_text(
        main_js_url,
        headers=script_headers,
        timeout=10,
        attempts=2,
    )
    bearer_match = re.search(r'AAAAAAAAAA[^"\']+', main_js)
    user_by_screen_name_match = re.search(
        r'queryId:"([^"]+)",operationName:"UserByScreenName"',
        main_js,
    )
    user_tweets_match = re.search(
        r'queryId:"([^"]+)",operationName:"UserTweets"',
        main_js,
    )
    if not bearer_match or not user_by_screen_name_match or not user_tweets_match:
        raise ValueError("未找到 X GraphQL 配置")
    bundle = {
        "bearer_token": bearer_match.group(0),
        "user_by_screen_name_query_id": user_by_screen_name_match.group(1),
        "user_tweets_query_id": user_tweets_match.group(1),
    }
    with x_client_lock:
        x_client_cache.clear()
        x_client_cache[main_js_url] = bundle
    return bundle


def fetch_json_from_x_api(
    url: str,
    profile_url: str,
    guest_token: str,
    bearer_token: str,
    source: dict[str, object],
) -> dict[str, object]:
    headers = source_headers(source)
    existing_cookie = header_value(headers, "Cookie")
    guest_cookie = f"gt={guest_token}"
    headers.update(
        {
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token}",
            "X-Guest-Token": guest_token,
            "X-Twitter-Active-User": "yes",
            "X-Twitter-Client-Language": "en",
            "Referer": profile_url,
            "Cookie": f"{existing_cookie}; {guest_cookie}" if existing_cookie else guest_cookie,
        }
    )
    return fetch_json_url(url, headers=headers, timeout=10, attempts=2)


def extract_x_guest_token(profile_html: str, response_headers: object) -> str:
    for pattern in (r"gt=([0-9]+)", r'"guest_token"\s*:\s*"([0-9]+)"'):
        match = re.search(pattern, profile_html)
        if match:
            return match.group(1)
    get_all = getattr(response_headers, "get_all", None)
    cookies = get_all("Set-Cookie") if callable(get_all) else []
    for cookie in cookies or []:
        match = re.search(r"(?:^|[;,]\s*)gt=([0-9]+)", str(cookie))
        if match:
            return match.group(1)
    return ""


def activate_x_guest_token(
    bearer_token: str,
    profile_url: str,
    source: dict[str, object],
) -> str:
    headers = source_headers(source)
    headers.update(
        {
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://x.com",
            "Referer": profile_url,
        }
    )
    last_error: Exception | None = None
    for endpoint in (
        "https://api.x.com/1.1/guest/activate.json",
        "https://api.twitter.com/1.1/guest/activate.json",
    ):
        try:
            payload = fetch_json_url(
                endpoint,
                method="POST",
                headers=headers,
                data=b"",
                timeout=10,
                attempts=2,
            )
        except (HTTPError, URLError, IncompleteRead, RemoteDisconnected, TimeoutError) as exc:
            last_error = exc
            continue
        guest_token = str(payload.get("guest_token") or "").strip()
        if guest_token:
            return guest_token
    message = "X 未提供访客令牌；请在自定义请求头中配置官方 X API Bearer Token"
    if last_error is not None:
        raise ValueError(f"{message}（{last_error}）") from last_error
    raise ValueError(message)


def fetch_x_api_v2_payload(
    handle: str,
    headers: dict[str, str],
) -> str:
    api_headers = dict(headers)
    api_headers["Accept"] = "application/json"
    user_url = f"https://api.x.com/2/users/by/username/{quote(handle, safe='')}"
    user_payload = fetch_json_url(user_url, headers=api_headers, timeout=10, attempts=2)
    user_data = user_payload.get("data", {})
    if not isinstance(user_data, dict) or not user_data.get("id"):
        detail = user_payload.get("detail") or user_payload.get("title") or "未返回用户 ID"
        raise ValueError(f"X API 无法获取账号 {handle}：{detail}")
    query = urlencode(
        {
            "max_results": MAX_X_MESSAGES_PER_SOURCE,
            "tweet.fields": "created_at,entities,lang,note_tweet",
        }
    )
    posts_url = f"https://api.x.com/2/users/{quote(str(user_data['id']), safe='')}/tweets?{query}"
    posts_payload = fetch_json_url(posts_url, headers=api_headers, timeout=10, attempts=2)
    posts_payload["loom_username"] = str(user_data.get("username") or handle)
    return json.dumps(posts_payload, ensure_ascii=False)


def fetch_x_public_timeline_payload(handle: str) -> str:
    if not X_PUBLIC_API_BASE_URL:
        raise ValueError(
            "X 公共时间线回退已禁用；请配置官方 X API Bearer Token"
        )
    endpoint = (
        f"{X_PUBLIC_API_BASE_URL}/profile/{quote(handle, safe='')}/statuses"
    )
    payload = fetch_json_url(
        endpoint,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        timeout=12,
        attempts=2,
    )
    response_code = payload.get("code")
    if response_code not in {None, 200}:
        raise ValueError(f"X 公共时间线返回错误：{response_code}")
    if not isinstance(payload.get("results"), list):
        raise ValueError("X 公共时间线未返回帖子列表")
    payload["loom_username"] = handle
    return json.dumps(payload, ensure_ascii=False)


def fetch_x_legacy_web_payload(source: dict[str, object], handle: str) -> str:
    headers = source_headers(source)
    profile_url = f"https://x.com/{quote(handle)}"
    profile_headers = dict(headers)
    profile_headers["Accept"] = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
    profile_html, response_headers = fetch_text(
        profile_url,
        headers=profile_headers,
        timeout=10,
        attempts=2,
    )
    profile_payload = extract_x_profile_payload(profile_html, handle)
    if profile_payload:
        return profile_payload
    client_bundle = get_x_client_bundle(profile_html, profile_headers, profile_url)
    guest_token = extract_x_guest_token(profile_html, response_headers)
    if not guest_token:
        guest_token = activate_x_guest_token(
            client_bundle["bearer_token"],
            profile_url,
            source,
        )
    user_variables = {"screen_name": handle, "withSafetyModeUserFields": True}
    user_lookup_url = (
        "https://x.com/i/api/graphql/"
        f"{client_bundle['user_by_screen_name_query_id']}/UserByScreenName"
        f"?variables={quote(json.dumps(user_variables, separators=(',', ':')))}"
        "&features=%7B%7D"
    )
    user_payload = fetch_json_from_x_api(
        user_lookup_url,
        profile_url,
        guest_token,
        client_bundle["bearer_token"],
        source,
    )
    user_result = (
        user_payload.get("data", {})
        .get("user", {})
        .get("result", {})
    )
    user_id = user_result.get("rest_id")
    if not user_id:
        raise ValueError("未能获取 X 账号信息")
    tweets_variables = {
        "userId": user_id,
        "count": MAX_X_MESSAGES_PER_SOURCE,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    tweets_features = {
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "tweetypie_unmention_optimization_enabled": True,
        "vibe_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "articles_preview_enabled": True,
    }
    tweets_lookup_url = (
        "https://x.com/i/api/graphql/"
        f"{client_bundle['user_tweets_query_id']}/UserTweets"
        f"?variables={quote(json.dumps(tweets_variables, separators=(',', ':')))}"
        f"&features={quote(json.dumps(tweets_features, separators=(',', ':')))}"
    )
    tweets_payload = fetch_json_from_x_api(
        tweets_lookup_url,
        profile_url,
        guest_token,
        client_bundle["bearer_token"],
        source,
    )
    return json.dumps(tweets_payload, ensure_ascii=False)


def _fetch_x_source_payload(source: dict[str, object]) -> str:
    handle = extract_x_handle(str(source["endpoint"]))
    headers = source_headers(source)
    authorization = header_value(headers, "Authorization").strip()
    if authorization.casefold().startswith("bearer "):
        return fetch_x_api_v2_payload(handle, headers)

    try:
        return fetch_x_legacy_web_payload(source, handle)
    except SOURCE_SYNC_ERRORS as direct_error:
        logger.info(
            "X 主页抓取不可用，改用公共时间线回退：%s (%s)",
            handle,
            type(direct_error).__name__,
        )
        try:
            return fetch_x_public_timeline_payload(handle)
        except SOURCE_SYNC_ERRORS as fallback_error:
            raise ValueError(
                "X 主页解析失败，公共时间线回退也不可用："
                f"{fallback_error}"
            ) from fallback_error


def fetch_x_source_payload(source: dict[str, object]) -> str:
    global x_network_blocked_until
    now = time.monotonic()
    with x_client_lock:
        blocked_until = x_network_blocked_until
    if blocked_until > now:
        wait_seconds = max(1, int(blocked_until - now))
        raise ValueError(f"X 网络当前不可达，将在约 {wait_seconds} 秒后重试")
    try:
        return _fetch_x_source_payload(source)
    except HTTPError:
        raise
    except TRANSIENT_NETWORK_ERRORS as exc:
        with x_client_lock:
            x_network_blocked_until = time.monotonic() + X_NETWORK_COOLDOWN_SECONDS
        raise ValueError(
            "无法连接 X；请检查网络、VPN，或为 Loom 配置 HTTPS_PROXY"
        ) from exc
    except ValueError as exc:
        cause = exc.__cause__
        if isinstance(cause, HTTPError) or not isinstance(cause, TRANSIENT_NETWORK_ERRORS):
            raise
        with x_client_lock:
            x_network_blocked_until = time.monotonic() + X_NETWORK_COOLDOWN_SECONDS
        raise ValueError(
            "无法连接 X；请检查网络、VPN，或为 Loom 配置 HTTPS_PROXY"
        ) from exc


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def child_text(element: ET.Element, *names: str) -> str | None:
    wanted = set(names)
    for child in element:
        if local_name(child.tag) in wanted and child.text:
            text = child.text.strip()
            if text:
                return text
    return None


def link_href(element: ET.Element) -> str | None:
    for child in element:
        if local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def strip_markup(value: str | None) -> str:
    if not value:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", value)
    return normalize_whitespace(html.unescape(no_tags))


def strip_inline_urls(value: str) -> str:
    without_urls = re.sub(r"https?://\S+|www\.\S+", " ", value)
    without_urls = re.sub(r"\(\s*\)", " ", without_urls)
    return normalize_whitespace(without_urls)


def parse_published_at(value: str | None) -> str:
    if not value:
        return utc_now_iso()
    normalized = normalize_whitespace(value.strip().replace("·", " "))
    relative_published_at = parse_relative_published_at(normalized)
    if relative_published_at is not None:
        return relative_published_at.isoformat()
    if normalized.isdigit():
        timestamp = int(normalized)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    iso_candidate = normalized.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for pattern in ("%I:%M %p %b %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(normalized)
        except (TypeError, ValueError):
            return utc_now_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def parse_relative_published_at(
    value: str | None, reference_time: datetime | None = None
) -> datetime | None:
    if not value:
        return None
    normalized = normalize_whitespace(value).lower()
    if normalized in {"刚刚", "just now", "now"}:
        return reference_time or datetime.now(UTC)
    patterns: tuple[tuple[str, str], ...] = (
        (r"^(\d+)\s*(?:s|sec|secs|second|seconds)$", "seconds"),
        (r"^(\d+)\s*(?:s|sec|secs|second|seconds)\s+ago$", "seconds"),
        (r"^(\d+)\s*(?:m|min|mins|minute|minutes)$", "minutes"),
        (r"^(\d+)\s*(?:m|min|mins|minute|minutes)\s+ago$", "minutes"),
        (r"^(\d+)\s*(?:h|hr|hrs|hour|hours)$", "hours"),
        (r"^(\d+)\s*(?:h|hr|hrs|hour|hours)\s+ago$", "hours"),
        (r"^(\d+)\s*(?:d|day|days)$", "days"),
        (r"^(\d+)\s*(?:d|day|days)\s+ago$", "days"),
        (r"^(\d+)\s*(?:w|week|weeks)$", "weeks"),
        (r"^(\d+)\s*(?:w|week|weeks)\s+ago$", "weeks"),
        (r"^(\d+)\s*秒前$", "seconds"),
        (r"^(\d+)\s*分钟前$", "minutes"),
        (r"^(\d+)\s*小时前$", "hours"),
        (r"^(\d+)\s*天前$", "days"),
        (r"^(\d+)\s*周前$", "weeks"),
    )
    for pattern, unit in patterns:
        match = re.match(pattern, normalized)
        if not match:
            continue
        amount = int(match.group(1))
        now = reference_time or datetime.now(UTC)
        return now - timedelta(**{unit: amount})
    return None


def canonicalize_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    query_pairs = [
        (key, item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"ref", "src"}
    ]
    query = "&".join(
        f"{quote(key, safe='')}={quote(item_value, safe='/:@')}" for key, item_value in query_pairs
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def detect_language(*values: str) -> str:
    combined = " ".join(value for value in values if value)
    return "zh" if re.search(r"[\u4e00-\u9fff]", combined) else "en"


def contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def truncate_text(value: str, limit: int) -> str:
    value = normalize_whitespace(value)
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1].rstrip()}…"


def build_summary(title: str, body: str) -> str:
    summary = strip_inline_urls(body or title)
    return truncate_text(summary, 120)


def build_original_content(title: str, body: str) -> str:
    content = strip_inline_urls(body or title)
    return truncate_text(content, 6000)


def build_content_context(message: dict[str, str], limit: int = 1200) -> str:
    content = normalize_whitespace(
        message.get("original_content")
        or message.get("summary_zh")
        or message.get("original_title")
        or ""
    )
    return truncate_text(content, limit)


def get_lmstudio_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def extract_json_object(text: str) -> dict[str, object]:
    decoder = json.JSONDecoder()
    for source_text in (text.strip(), re.sub(r"```(?:json)?|```", "", text).strip()):
        for match in re.finditer(r"\{", source_text):
            try:
                parsed, _ = decoder.raw_decode(source_text[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("LM Studio 返回内容不是有效 JSON")


def build_lmstudio_chat_payload(config: AIConfigResponse, prompt: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 Loom 的本地信息处理助手。"
                    "你必须只输出一个 JSON 对象，不要输出额外说明。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1600,
    }
    if "qwen" in config.model.lower():
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def call_lmstudio_chat(config: AIConfigResponse, api_key: str, prompt: str) -> dict[str, object]:
    request = Request(
        f"{config.base_url}/v1/chat/completions",
        data=json.dumps(
            build_lmstudio_chat_payload(config, prompt),
            ensure_ascii=False,
        ).encode("utf-8"),
        headers=get_lmstudio_headers(api_key),
        method="POST",
    )
    with urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise ValueError("LM Studio 未返回可用结果")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    reasoning_content = message.get("reasoning_content", "")
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        )
    if isinstance(reasoning_content, list):
        reasoning_content = "".join(
            str(item.get("text", "")) for item in reasoning_content if isinstance(item, dict)
        )
    if not isinstance(content, str) or not content.strip():
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            raise ValueError(
                "当前模型只返回了思考过程，未返回最终 JSON，"
                "请继续提高输出长度或改用非思考模型"
            )
        raise ValueError("LM Studio 返回内容为空")
    return extract_json_object(content)


def get_ai_runtime_settings() -> tuple[AIConfigResponse, str]:
    config = get_ai_config()
    api_key = get_system_state_map().get("ai_api_key", "")
    return config, api_key


def set_ai_runtime_status(status: str, error_message: str | None = None) -> None:
    updates: dict[str, object] = {"ai_status": status}
    if error_message is not None:
        updates["ai_last_error_message"] = error_message
    elif status == "ready":
        updates["ai_last_error_message"] = ""
    had_transaction = connection.in_transaction
    db = connection.cursor()
    try:
        set_system_state(db, updates)
        if not had_transaction:
            connection.commit()
    except sqlite3.OperationalError:
        if not had_transaction:
            connection.rollback()
    finally:
        db.close()


def test_ai_connection_with_config(
    config: AIConfigResponse, api_key: str
) -> AIConnectionTestResponse:
    try:
        request = Request(
            f"{config.base_url}/v1/models",
            headers=get_lmstudio_headers(api_key),
            method="GET",
        )
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status_code = response.status
        model_items = payload.get("data", [])
        available_models = [
            str(item.get("id"))
            for item in model_items
            if isinstance(item, dict) and item.get("id")
        ]
        if config.model not in available_models:
            return AIConnectionTestResponse(
                ok=False,
                status_code=status_code,
                message="LM Studio 已连接，但未找到当前配置的模型，请先在 LM Studio 中加载该模型。",
                available_models=available_models,
            )
        test_prompt = (
            '请输出 JSON：{"translated_title":"测试","summary_zh":"测试","category":"科技"}'
        )
        call_lmstudio_chat(config, api_key, test_prompt)
        return AIConnectionTestResponse(
            ok=True,
            status_code=status_code,
            message="LM Studio 连接成功，模型可用。",
            available_models=available_models,
        )
    except HTTPError as exc:
        return AIConnectionTestResponse(
            ok=False,
            status_code=exc.code,
            message=f"LM Studio 返回 HTTP {exc.code}",
            available_models=[],
        )
    except URLError as exc:
        return AIConnectionTestResponse(
            ok=False,
            status_code=None,
            message=f"连接失败：{exc.reason}",
            available_models=[],
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return AIConnectionTestResponse(
            ok=False,
            status_code=None,
            message=str(exc),
            available_models=[],
        )


def apply_ai_enrichment(
    source: dict[str, object], message: dict[str, str]
) -> tuple[dict[str, str], str | None]:
    config, api_key = get_ai_runtime_settings()
    if not ai_is_configured(config):
        return message, None
    if not config.translation_enabled and not config.classification_enabled:
        return message, None
    if message["language"] == "zh" and not config.classification_enabled:
        return message, None

    def build_prompt(
        force_chinese_translation: bool = False,
        content_limit: int = 1200,
    ) -> str:
        translation_requirement = (
            "1. translated_title 使用简洁中文标题；如果原文已是中文，保持原意。\n"
            "2. summary_zh 使用简洁中文摘要，长度尽量控制在 60 字以内。\n"
        )
        if force_chinese_translation and message["language"] != "zh":
            translation_requirement = (
                "1. translated_title 必须是简体中文标题，不能直接复用英文原文。\n"
                "2. summary_zh 必须是简体中文摘要，不能输出英文句子。\n"
            )
        return (
            "请基于以下消息返回 JSON，对象必须包含 "
            "translated_title、summary_zh、category 三个字段。\n"
            f"候选分类：{', '.join(config.categories)}\n"
            f"默认分类：{source['category']}\n"
            f"原始语言：{message['language']}\n"
            f"原标题：{message['original_title']}\n"
            f"原文正文：{build_content_context(message, content_limit)}\n"
            "要求：\n"
            f"{translation_requirement}"
            "3. category 必须严格从候选分类中选择一个；若无法判断，使用默认分类。"
        )

    def translation_is_valid(enriched_message: dict[str, str]) -> bool:
        if not config.translation_enabled or message["language"] == "zh":
            return True
        translated_title = normalize_whitespace(
            str(enriched_message.get("translated_title") or "")
        )
        translated_summary = normalize_whitespace(
            str(enriched_message.get("summary_zh") or "")
        )
        return contains_chinese(translated_title) and contains_chinese(translated_summary)

    try:
        def enrich_from_result(result: dict[str, object]) -> dict[str, str]:
            enriched = dict(message)
            if config.translation_enabled:
                translated_title_source = str(
                    result.get("translated_title") or enriched["original_title"]
                )
                translated_title = truncate_text(
                    normalize_whitespace(translated_title_source),
                    180,
                )
                summary_zh = truncate_text(
                    normalize_whitespace(
                        str(result.get("summary_zh") or enriched["summary_zh"])
                    ),
                    120,
                )
                enriched["translated_title"] = translated_title or enriched["translated_title"]
                enriched["summary_zh"] = summary_zh or enriched["summary_zh"]
            if config.classification_enabled:
                candidate = normalize_whitespace(str(result.get("category") or "")).strip()
                fallback_category = str(source["category"])
                enriched["category"] = (
                    candidate if candidate in config.categories else fallback_category
                )
            return enriched

        last_http_error: HTTPError | None = None
        enriched = dict(message)
        for content_limit in (1200, 700, 320):
            try:
                result = call_lmstudio_chat(
                    config,
                    api_key,
                    build_prompt(False, content_limit),
                )
                enriched = enrich_from_result(result)
                if translation_is_valid(enriched):
                    break
                result = call_lmstudio_chat(
                    config,
                    api_key,
                    build_prompt(True, content_limit),
                )
                enriched = enrich_from_result(result)
                if translation_is_valid(enriched):
                    break
            except HTTPError as exc:
                last_http_error = exc
                if exc.code == 400 and content_limit > 320:
                    continue
                raise
        if not translation_is_valid(enriched) and last_http_error is not None:
            raise last_http_error
        set_ai_runtime_status("ready")
        return enriched, None
    except (HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
        error_message = str(exc)
        set_ai_runtime_status("degraded", error_message)
        return message, error_message


def parse_feed_entries(source: dict[str, object], payload: str) -> list[dict[str, str]]:
    root = ET.fromstring(payload)
    category = str(source["category"])
    entries: list[dict[str, str]] = []
    if local_name(root.tag) == "feed":
        items = [child for child in root if local_name(child.tag) == "entry"]
        for item in items:
            title = child_text(item, "title") or ""
            body = strip_markup(child_text(item, "summary", "content"))
            link = canonicalize_url(link_href(item))
            if not title or not link:
                continue
            published_at = parse_published_at(child_text(item, "published", "updated"))
            language = detect_language(title, body)
            entries.append(
                {
                    "original_title": truncate_text(normalize_whitespace(title), 180),
                    "translated_title": title if language == "zh" else "",
                    "summary_zh": build_summary(title, body),
                    "original_content": build_original_content(title, body),
                    "language": language,
                    "category": category,
                    "original_url": link,
                    "published_at": published_at,
                }
            )
        return entries

    channel = next((child for child in root if local_name(child.tag) == "channel"), root)
    items = [child for child in channel if local_name(child.tag) == "item"]
    for item in items:
        title = child_text(item, "title") or ""
        description = child_text(item, "description", "encoded") or ""
        body = strip_markup(description)
        link = canonicalize_url(child_text(item, "link"))
        if link in {
            "https://www.treasurydirect.gov/auctions/auction-announcements/",
            "https://www.treasurydirect.gov/auctions/auction-results/",
        }:
            # TreasuryDirect reuses the listing URL for every item in each feed.
            auction_document = re.search(
                r'href=["\'](https://www\.treasurydirect\.gov/instit/[^"\'<>\s]+\.pdf)["\']',
                description,
            )
            if auction_document:
                link = auction_document.group(1)
        if not title or not link:
            continue
        published_at = parse_published_at(child_text(item, "pubDate", "published", "updated"))
        language = detect_language(title, body)
        entries.append(
            {
                "original_title": truncate_text(normalize_whitespace(title), 180),
                "translated_title": title if language == "zh" else "",
                "summary_zh": build_summary(title, body),
                "original_content": build_original_content(title, body),
                "language": language,
                "category": category,
                "original_url": link,
                "published_at": published_at,
            }
        )
    return entries


def parse_json_entries(source: dict[str, object], payload: str) -> list[dict[str, str]]:
    category = str(source["category"])
    decoded = json.loads(payload)
    if isinstance(decoded, list):
        items = decoded
    elif isinstance(decoded, dict):
        container = decoded.get("items") or decoded.get("data") or decoded.get("results") or []
        items = container if isinstance(container, list) else [container]
    else:
        items = []
    entries: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (
            item.get("title")
            or item.get("text")
            or item.get("headline")
            or item.get("name")
            or ""
        )
        body = strip_markup(
            item.get("summary")
            or item.get("content")
            or item.get("body")
            or item.get("description")
        )
        link = canonicalize_url(item.get("url") or item.get("link") or item.get("permalink"))
        if not title or not link:
            continue
        published_at = parse_published_at(
            item.get("published_at")
            or item.get("published")
            or item.get("created_at")
            or item.get("timestamp")
        )
        language = detect_language(str(title), body)
        title_text = truncate_text(normalize_whitespace(str(title)), 180)
        entries.append(
            {
                "original_title": title_text,
                "translated_title": title_text if language == "zh" else "",
                "summary_zh": build_summary(title_text, body),
                "original_content": build_original_content(title_text, body),
                "language": language,
                "category": category,
                "original_url": link,
                "published_at": published_at,
            }
        )
    return entries


def unwrap_x_tweet_result(result: object) -> dict[str, object] | None:
    if not isinstance(result, dict):
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        nested = result.get("tweet")
        return nested if isinstance(nested, dict) else None
    return result


def iter_x_timeline_tweet_results(payload: dict[str, object]) -> list[dict[str, object]]:
    instructions = (
        payload.get("data", {})
        .get("user", {})
        .get("result", {})
        .get("timeline", {})
        .get("timeline", {})
        .get("instructions", [])
    )
    tweet_results: list[dict[str, object]] = []
    for instruction in instructions:
        if not isinstance(instruction, dict):
            continue
        entries: list[object] = []
        if isinstance(instruction.get("entry"), dict):
            entries.append(instruction["entry"])
        if isinstance(instruction.get("entries"), list):
            entries.extend(instruction["entries"])
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            content = entry.get("content", {})
            if not isinstance(content, dict):
                continue
            item_content = content.get("itemContent", {})
            if isinstance(item_content, dict):
                tweet_result = unwrap_x_tweet_result(
                    item_content.get("tweet_results", {}).get("result")
                )
                if tweet_result:
                    tweet_results.append(tweet_result)
            if content.get("__typename") != "TimelineTimelineModule":
                continue
            for item in content.get("items", []):
                if not isinstance(item, dict):
                    continue
                item_content = item.get("item", {}).get("itemContent", {})
                if not isinstance(item_content, dict):
                    continue
                tweet_result = unwrap_x_tweet_result(
                    item_content.get("tweet_results", {}).get("result")
                )
                if tweet_result:
                    tweet_results.append(tweet_result)
    return tweet_results


def expand_x_text(text: str, entities: dict[str, object]) -> str:
    expanded = text
    for group_name in ("urls", "media"):
        replacements = entities.get(group_name, [])
        if not isinstance(replacements, list):
            continue
        for item in replacements:
            if not isinstance(item, dict):
                continue
            short_url = item.get("url")
            long_url = item.get("expanded_url") or item.get("display_url")
            if short_url and long_url:
                expanded = expanded.replace(str(short_url), str(long_url))
    return expanded


def parse_x_api_v2_entries(
    source: dict[str, object],
    decoded: dict[str, object],
) -> list[dict[str, str]] | None:
    posts = decoded.get("data")
    if not isinstance(posts, list):
        return None
    category = str(source["category"])
    fallback_handle = extract_x_handle(str(source["endpoint"]))
    screen_name = str(decoded.get("loom_username") or fallback_handle)
    entries: list[dict[str, str]] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = str(post.get("id") or "").strip()
        note_tweet = post.get("note_tweet", {})
        note_text = note_tweet.get("text") if isinstance(note_tweet, dict) else ""
        text = str(note_text or post.get("text") or "").strip()
        if not post_id or not text:
            continue
        entities = post.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}
        body = strip_inline_urls(normalize_whitespace(expand_x_text(text, entities)))
        title = truncate_text(body, 180)
        if not title:
            continue
        language = str(post.get("lang") or detect_language(body))
        entries.append(
            {
                "original_title": title,
                "translated_title": title if language == "zh" else "",
                "summary_zh": build_summary(title, body),
                "original_content": build_original_content(title, body),
                "language": language,
                "category": category,
                "original_url": canonicalize_url(
                    f"https://x.com/{quote(screen_name)}/status/{quote(post_id)}"
                ),
                "published_at": parse_published_at(str(post.get("created_at") or "")),
            }
        )
        if len(entries) >= MAX_X_MESSAGES_PER_SOURCE:
            break
    return entries


def parse_x_public_timeline_entries(
    source: dict[str, object],
    decoded: dict[str, object],
) -> list[dict[str, str]] | None:
    posts = decoded.get("results")
    if not isinstance(posts, list):
        return None
    category = str(source["category"])
    fallback_handle = extract_x_handle(str(source["endpoint"]))
    entries: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for post in posts:
        if not isinstance(post, dict):
            continue
        post_id = str(post.get("id") or "").strip()
        text = str(post.get("text") or "").strip()
        if not post_id or not text:
            continue
        entities = post.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}
        body = strip_inline_urls(normalize_whitespace(expand_x_text(text, entities)))
        title = truncate_text(body, 180)
        if not title:
            continue
        author = post.get("author", {})
        screen_name = (
            str(author.get("screen_name") or "").strip()
            if isinstance(author, dict)
            else ""
        )
        candidate_url = str(post.get("url") or "").strip()
        parsed_url = urlsplit(candidate_url)
        if (
            parsed_url.scheme.lower() not in {"http", "https"}
            or parsed_url.netloc.lower() not in X_HOSTS
        ):
            candidate_url = (
                f"https://x.com/{quote(screen_name or fallback_handle)}/status/"
                f"{quote(post_id)}"
            )
        url = canonicalize_url(candidate_url)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        language = str(post.get("lang") or detect_language(body))
        published_value = post.get("created_at") or post.get("created_timestamp") or ""
        entries.append(
            {
                "original_title": title,
                "translated_title": title if language == "zh" else "",
                "summary_zh": build_summary(title, body),
                "original_content": build_original_content(title, body),
                "language": language,
                "category": category,
                "original_url": url,
                "published_at": parse_published_at(str(published_value)),
            }
        )
        if len(entries) >= MAX_X_MESSAGES_PER_SOURCE:
            break
    return entries


def parse_x_entries(source: dict[str, object], payload: str) -> list[dict[str, str]]:
    category = str(source["category"])
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("X 接口未返回 JSON 对象")
    public_timeline_entries = parse_x_public_timeline_entries(source, decoded)
    if public_timeline_entries is not None:
        return public_timeline_entries
    api_v2_entries = parse_x_api_v2_entries(source, decoded)
    if api_v2_entries is not None:
        return api_v2_entries
    entries: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for tweet_result in iter_x_timeline_tweet_results(decoded):
        legacy = tweet_result.get("legacy", {})
        core = tweet_result.get("core", {})
        user_result = core.get("user_results", {}).get("result", {})
        user_core = user_result.get("core", {})
        rest_id = tweet_result.get("rest_id")
        screen_name = user_core.get("screen_name")
        if not isinstance(legacy, dict) or not rest_id or not screen_name:
            continue
        text = str(legacy.get("full_text") or "").strip()
        if not text:
            continue
        entities = legacy.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}
        body = strip_inline_urls(normalize_whitespace(expand_x_text(text, entities)))
        url = canonicalize_url(f"https://x.com/{screen_name}/status/{rest_id}")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        published_at = parse_published_at(
            str(
                legacy.get("created_at")
                or extract_x_relative_time_label(tweet_result)
                or ""
            )
        )
        language = detect_language(body)
        title = truncate_text(strip_inline_urls(body), 180)
        entries.append(
            {
                "original_title": title,
                "translated_title": title if language == "zh" else "",
                "summary_zh": build_summary(title, body),
                "original_content": build_original_content(title, body),
                "language": language,
                "category": category,
                "original_url": url,
                "published_at": published_at,
            }
        )
        if len(entries) >= MAX_X_MESSAGES_PER_SOURCE:
            break
    return entries


def extract_x_relative_time_label(tweet_result: dict[str, object]) -> str:
    time_text = tweet_result.get("timeText")
    if not isinstance(time_text, dict):
        return ""
    text = time_text.get("text")
    if isinstance(text, str) and text.strip():
        return text
    accessibility = time_text.get("accessibility", {})
    if not isinstance(accessibility, dict):
        return ""
    accessibility_data = accessibility.get("accessibilityData", {})
    if not isinstance(accessibility_data, dict):
        return ""
    label = accessibility_data.get("label")
    return label.strip() if isinstance(label, str) else ""


def parse_source_entries(
    source: dict[str, object], payload: str, content_type: str
) -> list[dict[str, str]]:
    source_type = str(source["source_type"])
    if source_type == "x":
        return parse_x_entries(source, payload)
    trimmed = payload.lstrip()
    if source_type in {"feed", "x"} or "xml" in content_type or trimmed.startswith("<"):
        return parse_feed_entries(source, payload)
    return parse_json_entries(source, payload)


def compute_importance_score(category: str, source_count: int, message_count: int) -> float:
    base = {
        "时政": 84,
        "AI": 82,
        "科技": 78,
        "宏观": 76,
        "数字货币": 72,
    }.get(category, 70)
    return float(min(base + source_count * 4 + min(message_count, 3) * 2, 99))


def compute_heat_score(source_count: int, message_count: int, latest_published_at: str) -> float:
    latest = parse_iso_datetime(latest_published_at) or datetime.now(UTC)
    age_minutes = max(0, int((datetime.now(UTC) - latest).total_seconds() // 60))
    freshness_bonus = max(0, 18 - min(age_minutes, 180) // 10)
    return float(min(48 + source_count * 6 + min(message_count, 5) * 5 + freshness_bonus, 99))


def refresh_cluster_stats(db, cluster_id: str) -> None:
    stats = db.execute(
        """
        SELECT COUNT(*) AS message_count,
               COUNT(DISTINCT m.source_id) AS source_count,
               MAX(m.published_at) AS latest_published_at
        FROM cluster_message_map cmm
        JOIN messages m ON m.message_id = cmm.message_id
        WHERE cmm.cluster_id = ?
        """,
        (cluster_id,),
    ).fetchone()
    cluster = db.execute(
        "SELECT primary_category FROM clusters WHERE cluster_id = ?",
        (cluster_id,),
    ).fetchone()
    if not stats or not cluster:
        return
    message_count = int(stats["message_count"] or 0)
    source_count = int(stats["source_count"] or 0)
    latest_published_at = stats["latest_published_at"] or utc_now_iso()
    db.execute(
        """
        UPDATE clusters
        SET source_count = ?, message_count = ?, latest_published_at = ?,
            importance_score = ?, heat_score = ?
        WHERE cluster_id = ?
        """,
        (
            source_count,
            message_count,
            latest_published_at,
            compute_importance_score(cluster["primary_category"], source_count, message_count),
            compute_heat_score(source_count, message_count, latest_published_at),
            cluster_id,
        ),
    )


def upsert_cluster_for_message(db, message: dict[str, str]) -> str:
    canonical_title = message["translated_title"] or message["original_title"]
    cluster = db.execute(
        """
        SELECT cluster_id, latest_published_at
        FROM clusters
        WHERE canonical_title = ? AND primary_category = ?
        ORDER BY latest_published_at DESC
        LIMIT 1
        """,
        (canonical_title, message["category"]),
    ).fetchone()
    if cluster:
        db.execute(
            """
            UPDATE clusters
            SET canonical_summary_zh = ?, original_link = ?,
                latest_published_at = MAX(latest_published_at, ?)
            WHERE cluster_id = ?
            """,
            (
                message["summary_zh"],
                message["original_url"],
                message["published_at"],
                cluster["cluster_id"],
            ),
        )
        return str(cluster["cluster_id"])

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
            canonical_title,
            message["summary_zh"],
            message["category"],
            compute_importance_score(message["category"], 1, 1),
            compute_heat_score(1, 1, message["published_at"]),
            1,
            1,
            message["published_at"],
            message["original_url"],
            "ready",
            utc_now_iso(),
        ),
    )
    return cluster_id


def rebuild_clusters(db) -> int:
    db.execute("DELETE FROM cluster_message_map")
    db.execute("DELETE FROM clusters")
    rows = db.execute(
        """
        SELECT message_id, original_title, translated_title, summary_zh,
               original_content, language, category, original_url, published_at
        FROM messages
        ORDER BY published_at ASC, created_at ASC
        """
    ).fetchall()
    cluster_ids: set[str] = set()
    for row in rows:
        message = {
            "original_title": str(row["original_title"]),
            "translated_title": str(row["translated_title"] or ""),
            "summary_zh": str(row["summary_zh"] or ""),
            "original_content": str(row["original_content"] or ""),
            "language": str(row["language"]),
            "category": str(row["category"]),
            "original_url": str(row["original_url"]),
            "published_at": str(row["published_at"]),
        }
        cluster_id = upsert_cluster_for_message(db, message)
        db.execute(
            "INSERT OR IGNORE INTO cluster_message_map (cluster_id, message_id) VALUES (?, ?)",
            (cluster_id, row["message_id"]),
        )
        cluster_ids.add(cluster_id)
    for cluster_id in cluster_ids:
        refresh_cluster_stats(db, cluster_id)
    return len(cluster_ids)


def update_message_content(
    db,
    existing,
    candidate_message: dict[str, str],
) -> bool:
    candidate_content = normalize_whitespace(str(candidate_message.get("original_content") or ""))
    existing_content = normalize_whitespace(str(existing["original_content"] or ""))
    if not candidate_content or len(candidate_content) <= len(existing_content):
        return False
    db.execute(
        "UPDATE messages SET original_content = ? WHERE message_id = ?",
        (candidate_content, str(existing["message_id"])),
    )
    return True


def persist_messages(
    source: dict[str, object], messages: list[dict[str, str]]
) -> tuple[int, int, int, int, str | None]:
    reference_time = datetime.now(UTC)
    added_messages = 0
    added_clusters: set[str] = set()
    updated_messages = 0
    ai_failed_messages = 0
    ai_error_message: str | None = None
    with cursor(connection) as db:
        for message in messages:
            if not is_within_sync_lookback(message["published_at"], reference_time):
                continue
            if not is_within_news_retention(message["published_at"], reference_time):
                continue
            existing = db.execute(
                """
                SELECT message_id, original_title, translated_title, summary_zh,
                       original_content, language, category, original_url, published_at
                FROM messages
                WHERE source_id = ? AND original_url = ?
                """,
                (source["source_id"], message["original_url"]),
            ).fetchone()
            if existing:
                if update_message_content(db, existing, message):
                    updated_messages += 1
                continue
            enriched_message, enrichment_error = apply_ai_enrichment(source, message)
            if enrichment_error:
                ai_failed_messages += 1
                if ai_error_message is None:
                    ai_error_message = enrichment_error
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
                    source["source_id"],
                    enriched_message["original_title"],
                    enriched_message["translated_title"] or None,
                    enriched_message["summary_zh"],
                    enriched_message["original_content"],
                    enriched_message["language"],
                    enriched_message["category"],
                    enriched_message["original_url"],
                    enriched_message["published_at"],
                    utc_now_iso(),
                ),
            )
            cluster_id = upsert_cluster_for_message(db, enriched_message)
            db.execute(
                "INSERT OR IGNORE INTO cluster_message_map (cluster_id, message_id) VALUES (?, ?)",
                (cluster_id, message_id),
            )
            refresh_cluster_stats(db, cluster_id)
            added_messages += 1
            added_clusters.add(cluster_id)
        if updated_messages:
            rebuild_clusters(db)
    return (
        added_messages,
        len(added_clusters),
        updated_messages,
        ai_failed_messages,
        ai_error_message,
    )


def sync_single_source(source: dict[str, object]) -> dict[str, object]:
    payload, content_type = fetch_source_payload(source)
    messages = parse_source_entries(source, payload, content_type)
    if not messages:
        raise ValueError("未解析到任何消息")
    added_messages, added_clusters, updated_messages, ai_failed_messages, ai_error_message = (
        persist_messages(source, messages)
    )
    health_status = "warning" if ai_error_message else "healthy"
    last_error_at = utc_now_iso() if ai_error_message else None
    last_error_message = (
        f"AI 翻译/分类失败 {ai_failed_messages} 条：{ai_error_message}"
        if ai_error_message
        else None
    )
    with cursor(connection) as db:
        db.execute(
            """
            UPDATE sources
            SET health_status = ?, last_success_at = ?, last_error_at = ?, last_error_message = ?
            WHERE source_id = ?
            """,
            (
                health_status,
                utc_now_iso(),
                last_error_at,
                last_error_message,
                source["source_id"],
            ),
        )
    return {
        "source_id": source["source_id"],
        "name": source["name"],
        "fetched_items": len(messages),
        "added_messages": added_messages,
        "added_clusters": added_clusters,
        "updated_messages": updated_messages,
        "ai_failed_messages": ai_failed_messages,
        "ai_error_message": ai_error_message,
    }


def run_sync(source_id: str | None = None) -> dict[str, object]:
    params: list[object] = []
    filters = ["enabled = 1"]
    if source_id:
        filters.append("source_id = ?")
        params.append(source_id)
    rows = connection.execute(
        f"{SOURCE_SELECT} WHERE {' AND '.join(filters)} ORDER BY created_at ASC",
        params,
    ).fetchall()
    summary: dict[str, object] = {
        "processed_sources": len(rows),
        "successful_sources": 0,
        "failed_sources": 0,
        "warning_sources": 0,
        "added_messages": 0,
        "added_clusters": 0,
        "updated_messages": 0,
        "results": [],
    }
    if not rows:
        with cursor(connection) as db:
            set_system_state(db, {"last_sync_at": utc_now_iso()})
        return summary

    for row in rows:
        source = dict(row)
        try:
            result = sync_single_source(source)
            summary["successful_sources"] += 1
            summary["added_messages"] += int(result["added_messages"])
            summary["added_clusters"] += int(result["added_clusters"])
            summary["updated_messages"] += int(result["updated_messages"])
            if result["ai_failed_messages"]:
                summary["warning_sources"] += 1
            summary["results"].append({"source_id": source["source_id"], "status": "ok", **result})
        except SOURCE_SYNC_ERRORS as exc:
            with cursor(connection) as db:
                db.execute(
                    """
                    UPDATE sources
                    SET health_status = ?, last_error_at = ?, last_error_message = ?
                    WHERE source_id = ?
                    """,
                    ("error", utc_now_iso(), str(exc), source["source_id"]),
                )
            summary["failed_sources"] += 1
            summary["results"].append(
                {
                    "source_id": source["source_id"],
                    "name": source["name"],
                    "status": "error",
                    "message": str(exc),
                }
            )

    network_status = "online" if summary["successful_sources"] else "degraded"
    if summary["failed_sources"] and summary["successful_sources"]:
        network_status = "degraded"
    with cursor(connection) as db:
        set_system_state(
            db,
            {
                "last_sync_at": utc_now_iso(),
                "network_status": network_status,
            },
        )
        cleanup_expired_news(db)
    return summary


def run_due_sync(reference_time: datetime | None = None) -> dict[str, object]:
    rows = list_due_source_rows(reference_time)
    summary: dict[str, object] = {
        "processed_sources": len(rows),
        "successful_sources": 0,
        "failed_sources": 0,
        "warning_sources": 0,
        "added_messages": 0,
        "added_clusters": 0,
        "updated_messages": 0,
        "results": [],
    }
    if not rows:
        return summary

    for source in rows:
        try:
            result = sync_single_source(source)
            summary["successful_sources"] += 1
            summary["added_messages"] += int(result["added_messages"])
            summary["added_clusters"] += int(result["added_clusters"])
            summary["updated_messages"] += int(result["updated_messages"])
            if result["ai_failed_messages"]:
                summary["warning_sources"] += 1
            summary["results"].append({"source_id": source["source_id"], "status": "ok", **result})
        except SOURCE_SYNC_ERRORS as exc:
            with cursor(connection) as db:
                db.execute(
                    """
                    UPDATE sources
                    SET health_status = ?, last_error_at = ?, last_error_message = ?
                    WHERE source_id = ?
                    """,
                    ("error", utc_now_iso(), str(exc), source["source_id"]),
                )
            summary["failed_sources"] += 1
            summary["results"].append(
                {
                    "source_id": source["source_id"],
                    "name": source["name"],
                    "status": "error",
                    "message": str(exc),
                }
            )

    network_status = "online" if summary["successful_sources"] else "degraded"
    if summary["failed_sources"] and summary["successful_sources"]:
        network_status = "degraded"
    with cursor(connection) as db:
        set_system_state(
            db,
            {
                "last_sync_at": utc_now_iso(),
                "network_status": network_status,
            },
        )
        cleanup_expired_news(db)
    return summary


async def sync_scheduler_loop() -> None:
    while True:
        await asyncio.sleep(SYNC_TICK_SECONDS)
        try:
            async with sync_lock:
                result = await asyncio.to_thread(run_due_sync)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("后台同步失败，调度器将在下一轮继续")
            continue
        if not result["processed_sources"] and not result["updated_messages"]:
            continue
        emit("sources_changed")
        if result["added_messages"] or result["updated_messages"]:
            emit("timeline_changed")


@app.get("/", response_class=FileResponse)
async def root() -> str:
    return "loom_app/static/index.html"


@app.get("/timeline", response_class=FileResponse)
async def timeline_page() -> str:
    return "loom_app/static/index.html"


@app.get("/sources", response_class=FileResponse)
async def sources_page() -> str:
    return "loom_app/static/index.html"


@app.get("/system", response_class=FileResponse)
async def system_page() -> str:
    return "loom_app/static/index.html"


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "time": utc_now_iso()}


@app.get("/api/sources", response_model=list[SourceOut])
async def get_sources() -> list[SourceOut]:
    return list_sources()


@app.post("/api/sources", response_model=SourceOut, status_code=201)
async def create_source(payload: SourceCreate) -> SourceOut:
    created_source = create_source_record(payload)
    emit("sources_changed")
    return created_source


@app.post("/api/sources/bulk-x", response_model=XBulkImportResponse, status_code=201)
async def bulk_import_x_sources(payload: XBulkImportRequest) -> XBulkImportResponse:
    handles = parse_bulk_x_handles(payload.accounts_text)
    rows = connection.execute(
        f"{SOURCE_SELECT} WHERE source_type = ? ORDER BY created_at ASC",
        ("x",),
    ).fetchall()
    existing_handles: set[str] = set()
    for row in rows:
        try:
            existing_handles.add(extract_x_handle(str(row["endpoint"])).lower())
        except ValueError:
            continue
    created: list[SourceOut] = []
    skipped_handles: list[str] = []
    for handle in handles:
        lowered = handle.lower()
        if lowered in existing_handles:
            skipped_handles.append(handle)
            continue
        created.append(
            create_source_record(
                SourceCreate(
                    name=handle,
                    source_type="x",
                    endpoint=f"https://x.com/{handle}",
                    method="GET",
                    category=payload.category,
                    poll_interval_minutes=payload.poll_interval_minutes,
                    enabled=payload.enabled,
                    headers={},
                )
            )
        )
        existing_handles.add(lowered)
    if created:
        emit("sources_changed")
    return XBulkImportResponse(created=created, skipped_handles=skipped_handles)


@app.put("/api/sources/{source_id}", response_model=SourceOut)
async def update_source(source_id: str, payload: SourceUpdate) -> SourceOut:
    now = utc_now_iso()
    with cursor(connection) as db:
        result = db.execute(
            """
            UPDATE sources
            SET name = ?, source_type = ?, endpoint = ?, method = ?, category = ?,
                poll_interval_minutes = ?, enabled = ?, headers_json = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (
                payload.name,
                payload.source_type,
                str(payload.endpoint),
                payload.method.upper(),
                payload.category,
                payload.poll_interval_minutes,
                int(payload.enabled),
                json.dumps(payload.headers, ensure_ascii=False),
                now,
                source_id,
            ),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="来源不存在")
    emit("sources_changed")
    row = connection.execute(
        f"{SOURCE_SELECT} WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    return row_to_source(row)


@app.patch("/api/sources/{source_id}/status", response_model=SourceOut)
async def update_source_status(source_id: str, payload: SourceStatusUpdate) -> SourceOut:
    now = utc_now_iso()
    with cursor(connection) as db:
        result = db.execute(
            """
            UPDATE sources
            SET enabled = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (int(payload.enabled), now, source_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="来源不存在")
    emit("sources_changed")
    row = connection.execute(
        f"{SOURCE_SELECT} WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    return row_to_source(row)


@app.delete("/api/sources/{source_id}", status_code=204)
async def delete_source(source_id: str) -> None:
    with cursor(connection) as db:
        result = db.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="来源不存在")
    emit("sources_changed")


@app.post("/api/sources/test", response_model=SourceTestResponse)
async def test_source(payload: SourceTestRequest) -> SourceTestResponse:
    source: dict[str, object] = {
        "source_type": payload.source_type,
        "endpoint": str(payload.endpoint),
        "method": payload.method.upper(),
        "category": payload.category,
        "headers": payload.headers,
    }
    try:
        raw_payload, content_type = await asyncio.to_thread(fetch_source_payload, source)
        messages = parse_source_entries(source, raw_payload, content_type)
        if not messages:
            raise ValueError("连接成功，但未解析到任何消息")
        source_label = "X 账号" if payload.source_type == "x" else "来源"
        return SourceTestResponse(
            ok=True,
            status_code=200,
            message=f"{source_label}连接并解析成功，共 {len(messages)} 条",
        )
    except SOURCE_SYNC_ERRORS as exc:
        status_code = exc.code if isinstance(exc, HTTPError) else None
        message = f"目标返回 HTTP {exc.code}" if isinstance(exc, HTTPError) else str(exc)
        return SourceTestResponse(ok=False, status_code=status_code, message=message)


@app.get("/api/ai/config", response_model=AIConfigResponse)
async def get_ai_config_endpoint() -> AIConfigResponse:
    return get_ai_config()


@app.put("/api/ai/config", response_model=AIConfigResponse)
async def update_ai_config(payload: AIConfigUpdate) -> AIConfigResponse:
    normalized_base_url = normalize_lmstudio_base_url(str(payload.base_url))
    categories = normalize_ai_categories(payload.categories)
    existing_state = get_system_state_map()
    if payload.clear_api_key:
        api_key = ""
    elif payload.api_key is None:
        api_key = existing_state.get("ai_api_key", "")
    else:
        api_key = payload.api_key.strip()
    with cursor(connection) as db:
        set_system_state(
            db,
            {
                "ai_base_url": normalized_base_url,
                "ai_model": payload.model.strip(),
                "ai_api_key": api_key,
                "ai_translation_enabled": payload.translation_enabled,
                "ai_classification_enabled": payload.classification_enabled,
                "ai_categories": categories,
                "ai_status": "configured",
                "ai_last_error_message": "",
            },
        )
    emit("system_changed")
    return get_ai_config()


@app.post("/api/ai/test", response_model=AIConnectionTestResponse)
async def test_ai_connection(payload: AIConfigUpdate) -> AIConnectionTestResponse:
    existing_state = get_system_state_map()
    if payload.clear_api_key:
        api_key = ""
    elif payload.api_key is None:
        api_key = existing_state.get("ai_api_key", "")
    else:
        api_key = payload.api_key.strip()
    config = AIConfigResponse(
        base_url=normalize_lmstudio_base_url(str(payload.base_url)),
        model=payload.model.strip(),
        has_api_key=bool(api_key),
        translation_enabled=payload.translation_enabled,
        classification_enabled=payload.classification_enabled,
        categories=normalize_ai_categories(payload.categories),
    )
    result = test_ai_connection_with_config(config, api_key)
    status = "ready" if result.ok else "degraded"
    error_message = None if result.ok else result.message
    set_ai_runtime_status(status, error_message)
    emit("system_changed")
    return result


@app.get("/api/timeline/clusters", response_model=TimelineResponse)
async def get_timeline(
    category: str | None = None,
    high_importance_only: bool = False,
    sort_by: str = Query("latest", pattern="^(latest|importance|heat)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> TimelineResponse:
    filters: list[str] = []
    params: list[object] = []
    if category:
        filters.append("primary_category = ?")
        params.append(category)
    if high_importance_only:
        filters.append("importance_score >= 80")
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    order_by = {
        "latest": "latest_published_at DESC",
        "importance": "importance_score DESC, latest_published_at DESC",
        "heat": "heat_score DESC, latest_published_at DESC",
    }[sort_by]
    total = connection.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM clusters c
        {where_clause}
        """,
        params,
    ).fetchone()["count"]
    rows = connection.execute(
        f"""
        SELECT c.cluster_id, c.canonical_title,
               (
                   SELECT m.original_title
                   FROM cluster_message_map cmm
                   JOIN messages m ON m.message_id = cmm.message_id
                   WHERE cmm.cluster_id = c.cluster_id
                   ORDER BY m.published_at DESC, m.created_at DESC
                   LIMIT 1
               ) AS canonical_original_title,
               c.canonical_summary_zh,
               (
                   SELECT m.language
                   FROM cluster_message_map cmm
                   JOIN messages m ON m.message_id = cmm.message_id
                   WHERE cmm.cluster_id = c.cluster_id
                   ORDER BY m.published_at DESC, m.created_at DESC
                   LIMIT 1
               ) AS canonical_language,
               c.primary_category, c.importance_score, c.heat_score,
               c.source_count, c.message_count, c.latest_published_at,
               c.original_link, c.ai_status
        FROM clusters c
        {where_clause}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    return TimelineResponse(
        clusters=[dict(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


@app.get("/api/clusters/{cluster_id}/messages", response_model=list[ClusterMessageOut])
async def get_cluster_messages(cluster_id: str) -> list[ClusterMessageOut]:
    rows = connection.execute(
        """
        SELECT m.message_id, s.name AS source_name, m.original_title, m.translated_title,
               m.summary_zh, m.original_content, m.language, m.category,
               m.original_url, m.published_at
        FROM cluster_message_map cmm
        JOIN messages m ON m.message_id = cmm.message_id
        JOIN sources s ON s.source_id = m.source_id
        WHERE cmm.cluster_id = ?
        ORDER BY m.published_at DESC
        """,
        (cluster_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="聚合卡片不存在")
    return [ClusterMessageOut.model_validate(dict(row)) for row in rows]


@app.get("/api/system/status", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    state = get_system_state_map()
    ai_config = get_ai_config()
    ai_status = state.get("ai_status", "unknown")
    if not ai_is_configured(ai_config):
        ai_status = "not_configured"
    enabled_sources = connection.execute(
        "SELECT COUNT(*) AS count FROM sources WHERE enabled = 1"
    ).fetchone()["count"]
    stored_messages = connection.execute(
        "SELECT COUNT(*) AS count FROM messages"
    ).fetchone()["count"]
    stored_clusters = connection.execute(
        "SELECT COUNT(*) AS count FROM clusters"
    ).fetchone()["count"]
    return SystemStatusResponse(
        network_status=state.get("network_status", "unknown"),
        ai_status=ai_status,
        ai_last_error_message=normalize_whitespace(state.get("ai_last_error_message", "")) or None,
        ai_base_url=ai_config.base_url if ai_is_configured(ai_config) else None,
        ai_model=ai_config.model or None,
        ai_translation_enabled=ai_config.translation_enabled,
        ai_classification_enabled=ai_config.classification_enabled,
        last_sync_at=state.get("last_sync_at"),
        last_cleanup_at=state.get("last_cleanup_at"),
        enabled_sources=enabled_sources,
        stored_messages=stored_messages,
        stored_clusters=stored_clusters,
    )


@app.post("/api/system/sync")
async def run_system_sync(payload: SyncRequest | None = None) -> dict[str, object]:
    target_source_id = payload.source_id if payload else None
    if target_source_id and get_source_row(target_source_id) is None:
        raise HTTPException(status_code=404, detail="来源不存在")
    async with sync_lock:
        result = await asyncio.to_thread(run_sync, target_source_id)
    emit("sources_changed")
    if result["added_messages"] or result["updated_messages"]:
        emit("timeline_changed")
    return result


@app.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = hub.subscribe()
    await websocket.send_json({"type": "connected", "sent_at": utc_now_iso()})
    try:
        while True:
            try:
                payload = await asyncio.to_thread(queue.get, True, 15)
                await websocket.send_text(payload)
            except Empty:
                await websocket.send_json({"type": "heartbeat", "sent_at": utc_now_iso()})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(queue)
