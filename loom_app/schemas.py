from __future__ import annotations

from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, HttpUrl

SourceType = Literal["api", "feed", "x"]
SortBy = Literal["latest", "importance", "heat"]


class SourceBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    source_type: SourceType
    endpoint: HttpUrl
    method: str = Field(default="GET", min_length=3, max_length=10)
    category: str = Field(default="其他", min_length=1, max_length=30)
    poll_interval_minutes: int = Field(default=15, ge=1, le=1440)
    enabled: bool = True
    headers: dict[str, Any] = Field(default_factory=dict)


class SourceCreate(SourceBase):
    pass


class SourceUpdate(SourceBase):
    pass


class SourceStatusUpdate(BaseModel):
    enabled: bool


class SourceOut(SourceBase):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    health_status: str
    last_success_at: str | None
    last_error_at: str | None
    last_error_message: str | None
    created_at: str
    updated_at: str


class ClusterMessageOut(BaseModel):
    message_id: str
    source_name: str
    original_title: str
    translated_title: str | None
    summary_zh: str | None
    original_content: str | None
    language: str
    category: str
    original_url: str
    published_at: str


class ClusterOut(BaseModel):
    cluster_id: str
    canonical_title: str
    canonical_original_title: str | None = None
    canonical_summary_zh: str
    canonical_language: str | None = None
    primary_category: str
    importance_score: float
    heat_score: float
    source_count: int
    message_count: int
    latest_published_at: str
    original_link: str
    ai_status: str


class TimelineResponse(BaseModel):
    clusters: list[ClusterOut]
    total: int
    limit: int
    offset: int
    has_more: bool


class SystemStatusResponse(BaseModel):
    network_status: str
    ai_status: str
    ai_last_error_message: str | None
    ai_base_url: str | None
    ai_model: str | None
    ai_translation_enabled: bool
    ai_classification_enabled: bool
    last_sync_at: str | None
    last_cleanup_at: str | None
    enabled_sources: int
    stored_messages: int
    stored_clusters: int


class SourceTestRequest(BaseModel):
    endpoint: HttpUrl
    method: str = "GET"
    source_type: SourceType = "api"


class XBulkImportRequest(BaseModel):
    accounts_text: str = Field(min_length=1, max_length=5000)
    category: str = Field(default="科技", min_length=1, max_length=30)
    poll_interval_minutes: int = Field(default=15, ge=1, le=1440)
    enabled: bool = True


class XBulkImportResponse(BaseModel):
    created: list[SourceOut]
    skipped_handles: list[str]


class AIConfigUpdate(BaseModel):
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False
    translation_enabled: bool = True
    classification_enabled: bool = True
    categories: list[str] = Field(min_length=1, max_length=20)


class AIConfigResponse(BaseModel):
    base_url: str
    model: str
    has_api_key: bool
    translation_enabled: bool
    classification_enabled: bool
    categories: list[str]


class AIConnectionTestResponse(BaseModel):
    ok: bool
    status_code: int | None = None
    message: str
    available_models: list[str] = Field(default_factory=list)


class SyncRequest(BaseModel):
    source_id: str | None = None


class SourceTestResponse(BaseModel):
    ok: bool
    status_code: int | None = None
    message: str
