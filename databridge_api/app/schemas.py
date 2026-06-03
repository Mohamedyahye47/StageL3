from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

class AiRecommendationIn(BaseModel):
    user_request: str = Field(min_length=3, max_length=1000)


class AiRuntimeConfigIn(BaseModel):
    AI_PROVIDER: str = "local"
    AI_MODEL: str = "regles_metier_locales"
    AI_TEMPERATURE: float = Field(default=0, ge=0, le=1)
    AI_TIMEOUT_SECONDS: int = Field(default=60, ge=1, le=300)
    AI_ENABLE_BUSINESS_RULES: bool = True
    AI_MAX_CANDIDATES: int = Field(default=40, ge=10, le=100)
    AI_TARGET_INDICATORS: int = Field(default=5, ge=1, le=20)
    WB_MAX_INDICATORS_PER_DATASET: int = Field(default=60, ge=1, le=60)


class AiProviderRuntimeOption(BaseModel):
    code: str
    label: str
    models: list[str] = Field(default_factory=list)
    default_model: str = ""
    implemented: bool = False
    configured: bool = False
    available: bool = False
    json_capability: str = "unsupported"
    supported_layers: list[str] = Field(default_factory=list)
    disabled_reason: str | None = None
    key_envs: list[str] = Field(default_factory=list)


class AiRuntimeConfigOut(AiRuntimeConfigIn):
    message: str
    persistence: str = "runtime_memory"
    providers_by_layer: dict[str, list[AiProviderRuntimeOption]] = Field(default_factory=dict)
    disabled_providers_by_layer: dict[str, list[AiProviderRuntimeOption]] = Field(default_factory=dict)
    models_by_layer: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


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


class ExportDatasetVersionOut(BaseModel):
    id: int
    version: int
    export_version: str
    start_date: str
    end_date: str
    format: str
    frequency: str
    csv_url: str
    json_url: str
    generated_at: str
    country: CountryOut
    indicators: list[IndicatorOut] = Field(default_factory=list)
    manifest: dict[str, Any]


# FIX: Was missing row_count, non_null_value_count, missing_indicator_codes.
# The publish_service.get_dataset_version_data_preview() returns all these fields,
# but the old schema caused Pydantic to strip them before sending to the client.
# The detail-preview template (partials/dataset_detail_preview.html) needs them
# to render the metrics cards correctly.
class ExportDatasetDataPreviewOut(BaseModel):
    data_url: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    preview_count: int
    row_count: int = 0
    non_null_value_count: int = 0
    missing_indicator_codes: list[str] = Field(default_factory=list)


class DatasetBuildPreviewOut(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    preview_count: int
    row_count: int
    non_null_value_count: int
    missing_indicator_codes: list[str] = Field(default_factory=list)
    country: CountryOut
    indicators: list[IndicatorOut] = Field(default_factory=list)


class ExportDatasetListOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    country: CountryOut
    latest_version: int
    updated_at: str
    provider: str
    export_id: str
    csv_url: str | None = None
    json_url: str | None = None
    last_export_status: str | None = None


class ExportDatasetDetailOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    provider: str
    export_id: str
    csv_url: str | None = None
    json_url: str | None = None
    status: str
    latest_version: int
    created_at: str
    updated_at: str
    latest_version_detail: ExportDatasetVersionOut
    versions: list[ExportDatasetVersionOut] = Field(default_factory=list)
    opendatasoft_metadata: dict[str, Any] | None = None
    opendatasoft_status: str | None = None
    opendatasoft_public_url: str | None = None
    opendatasoft_last_error: str | None = None
    opendatasoft_last_steps: list[dict[str, Any]] = Field(default_factory=list)
    opendatasoft_last_result: dict[str, Any] | None = None


class ExportLogOut(BaseModel):
    id: int
    export_dataset_id: int | None = None
    action: str
    row_count: int | None = None
    non_null_value_count: int | None = None
    status: str
    error_message: str | None = None
    duration_seconds: float | None = None
    created_at: str


class OpenDataSoftMetadataOut(BaseModel):
    slug: str
    opendatasoft_metadata: dict[str, Any]
    opendatasoft_status: str | None = None
    opendatasoft_public_url: str | None = None
    opendatasoft_last_error: str | None = None
    opendatasoft_last_steps: list[dict[str, Any]] = Field(default_factory=list)
    opendatasoft_last_result: dict[str, Any] | None = None


class OpenDataSoftPublishOut(BaseModel):
    status: str
    dry_run: bool
    dataset_id: str
    public_url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    opendatasoft_last_error: str | None = None
    opendatasoft_last_steps: list[dict[str, Any]] = Field(default_factory=list)


class ExportLinksOut(BaseModel):
    slug: str
    title: str
    description: str
    csv_url: str
    json_url: str
    row_count: int
    non_null_value_count: int
    indicator_count: int
    status: str
    opendatasoft_metadata: dict[str, Any] | None = None
    opendatasoft_status: str | None = None
    opendatasoft_public_url: str | None = None
