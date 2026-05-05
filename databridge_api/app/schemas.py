from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

class AiRecommendationIn(BaseModel):
    user_request: str = Field(min_length=3, max_length=1000)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    base_url: str | None = None
    description: str | None = None
    created_at: str | None = None


class TopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    name: str
    description: str | None = None


class IndicatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    code: str
    name: str
    description: str | None = None
    unit: str | None = None
    periodicity: str | None = None
    topic_ids: list[int] = Field(default_factory=list)


class CountryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code_iso3: str
    code_iso2: str
    wb_code: str
    name: str
    region: str | None = None
    enabled: bool


class PublishDatasetIn(BaseModel):
    source_code: str
    topic_id: int | None = None
    indicator_ids: list[int] = Field(min_length=1)
    country_id: int
    start_date: str
    end_date: str
    title: str
    description: str
    existing_slug: str | None = None
    format: str = "csv"
    frequency: str | None = None


class PublishedDatasetVersionOut(BaseModel):
    id: int
    version: int
    remote_version: str
    start_date: str
    end_date: str
    format: str
    frequency: str
    manifest_url: str
    published_at: str
    country: CountryOut
    indicators: list[IndicatorOut] = Field(default_factory=list)
    manifest: dict[str, Any]


class PublishedDatasetDataPreviewOut(BaseModel):
    data_url: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    preview_count: int


class DatasetBuildPreviewOut(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    preview_count: int
    row_count: int
    non_null_value_count: int
    missing_indicator_codes: list[str] = Field(default_factory=list)
    country: CountryOut
    indicators: list[IndicatorOut] = Field(default_factory=list)


class PublishedDatasetListOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    country: CountryOut
    latest_version: int
    published_at: str
    remote_provider: str
    remote_id: str
    remote_url: str
    last_publish_status: str | None = None


class PublishedDatasetDetailOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    remote_provider: str
    remote_id: str
    remote_url: str
    visibility: str
    status: str
    latest_version: int
    created_at: str
    published_at: str
    updated_at: str
    latest_version_detail: PublishedDatasetVersionOut
    versions: list[PublishedDatasetVersionOut] = Field(default_factory=list)


class PublishResultOut(BaseModel):
    dataset_id: int
    version_id: int
    slug: str
    title: str
    description: str
    version: int
    remote_version: str
    remote_id: str
    remote_url: str
    manifest_url: str
    published_at: str
    country: CountryOut
    indicators: list[IndicatorOut] = Field(default_factory=list)
    manifest: dict[str, Any]


class HfHealthOut(BaseModel):
    ok: bool
    namespace: str | None = None
    visibility: str | None = None
    remote_provider: str = "huggingface"
    message: str
