from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = PROJECT_ROOT / "databridge_api"
for path in (PROJECT_ROOT, API_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import config as app_config
from app.ai.clients import AIProviderExecutionError
from app.ai.registry import AIProviderConfigError, models_by_layer, providers_by_layer, validate_layer_config
from app.database import get_db
from app.schemas import (
    AiRuntimeConfigIn,
    AiRuntimeConfigOut,
    CountryOut,
    DatasetBuildPreviewOut,
    ExportDatasetDataPreviewOut,
    ExportDatasetDetailOut,
    ExportDatasetListOut,
    ExportLinksOut,
    IndicatorOut,
    OpenDataSoftMetadataOut,
    OpenDataSoftPublishOut,
    PublishDatasetIn,
    SourceOut,
    TopicOut,
)
from app.services import ai_assistant_service, ai_business_rules
from app.services.ai_assistant_service import AIQuotaExceeded, recommend_dataset_validated
from app.services.catalog_service import list_countries, list_indicators, list_sources, list_topics
from app.services.chart_service import build_export_chronology_png
from app.services.dashboard_metrics_service import build_dashboard_metrics
from app.services.publish_service import (
    PublishError,
    check_export_mode as _check_export_mode,
    delete_export_dataset as _delete_export_dataset,
    generate_export_links as _generate_export_links,
    get_dashboard_datasets,
    get_dataset_detail as _get_dataset_detail,
    get_dataset_version_data_preview,
    get_opendatasoft_metadata as _get_opendatasoft_metadata,
    prepare_dataset_for_opendatasoft,
    preview_dataset as _preview_dataset,
)


class BackendUnavailable(Exception):
    """Kept for view compatibility; internal service failures now raise ApiError."""


class ApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


@contextmanager
def _db_session() -> Iterator[Any]:
    generator = get_db()
    db = next(generator)
    try:
        yield db
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


def _dump(schema: type[Any], value: Any) -> dict[str, Any]:
    return schema.model_validate(value).model_dump(mode="json")


def _dump_many(schema: type[Any], values: list[Any]) -> list[dict[str, Any]]:
    return [_dump(schema, value) for value in values]


def _publish_payload(payload: dict[str, Any]) -> PublishDatasetIn:
    try:
        return PublishDatasetIn.model_validate(payload)
    except Exception as exc:
        raise ApiError(str(exc), status_code=400) from exc


def _handle_publish_error(exc: PublishError) -> ApiError:
    return ApiError(str(exc), status_code=getattr(exc, "status_code", 400))


def get_sources() -> list[dict[str, Any]]:
    with _db_session() as db:
        return _dump_many(SourceOut, list_sources(db))


def get_topics(*, source_code: str | None = None, source_id: int | None = None, search: str = "") -> list[dict[str, Any]]:
    with _db_session() as db:
        return _dump_many(TopicOut, list_topics(db, source_code=source_code, source_id=source_id, search=search))


def get_indicators(
    *,
    source_code: str | None = None,
    topic_id: int | None = None,
    search: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    with _db_session() as db:
        return _dump_many(
            IndicatorOut,
            list_indicators(db, source_code=source_code, topic_id=topic_id, search=search, limit=limit, offset=offset),
        )


def get_source_limits() -> dict[str, Any]:
    return {
        code: {
            "max_indicators_per_dataset": int(values["max_indicators_per_dataset"]),
            "label": values["label"],
        }
        for code, values in app_config.SOURCE_LIMITS.items()
    }


def get_countries(*, search: str = "", limit: int = 50) -> list[dict[str, Any]]:
    with _db_session() as db:
        return _dump_many(CountryOut, list_countries(db, search=search, limit=limit))


def generate_export_links(payload: dict[str, Any]) -> dict[str, Any]:
    with _db_session() as db:
        try:
            return ExportLinksOut.model_validate(_generate_export_links(db, _publish_payload(payload))).model_dump(mode="json")
        except PublishError as exc:
            raise _handle_publish_error(exc) from exc


def preview_dataset(payload: dict, limit: int = 50) -> dict:
    with _db_session() as db:
        try:
            return DatasetBuildPreviewOut.model_validate(_preview_dataset(db, _publish_payload(payload), limit=limit)).model_dump(mode="json")
        except PublishError as exc:
            raise _handle_publish_error(exc) from exc


def get_export_datasets() -> list[dict[str, Any]]:
    with _db_session() as db:
        return _dump_many(ExportDatasetListOut, get_dashboard_datasets(db))


def get_dataset_detail(slug: str) -> dict[str, Any]:
    with _db_session() as db:
        detail = _get_dataset_detail(db, slug)
        if detail is None:
            raise ApiError(f"Dataset '{slug}' introuvable.", status_code=404)
        return ExportDatasetDetailOut.model_validate(detail).model_dump(mode="json")


def get_opendatasoft_metadata(slug: str) -> dict[str, Any]:
    with _db_session() as db:
        try:
            return OpenDataSoftMetadataOut.model_validate(_get_opendatasoft_metadata(db, slug)).model_dump(mode="json")
        except PublishError as exc:
            raise _handle_publish_error(exc) from exc


def prepare_opendatasoft(slug: str) -> dict[str, Any]:
    with _db_session() as db:
        try:
            return OpenDataSoftPublishOut.model_validate(prepare_dataset_for_opendatasoft(db, slug)).model_dump(mode="json")
        except PublishError as exc:
            raise _handle_publish_error(exc) from exc


def get_dataset_preview(slug: str, version: int, *, limit: int = 25) -> dict[str, Any]:
    with _db_session() as db:
        try:
            preview = get_dataset_version_data_preview(db, slug, version, limit=limit)
        except PublishError as exc:
            raise _handle_publish_error(exc) from exc
        if preview is None:
            raise ApiError(f"Version '{version}' introuvable pour '{slug}'.", status_code=404)
        return ExportDatasetDataPreviewOut.model_validate(preview).model_dump(mode="json")


def get_ai_dataset_recommendation(user_request: str, *, local_only: bool = False) -> dict:
    if not user_request or not user_request.strip():
        raise ValueError("La demande utilisateur est obligatoire.")
    with _db_session() as db:
        try:
            result = recommend_dataset_validated(
                db,
                user_request.strip(),
                source_execution="assistant_ui",
                triggered_by="user_click",
                local_only=local_only,
            )
        except AIQuotaExceeded as exc:
            raise ApiError(exc.to_payload()["message"], status_code=429, payload=exc.to_payload()) from exc
        except AIProviderConfigError as exc:
            raise ApiError(str(exc), status_code=400) from exc
        except AIProviderExecutionError as exc:
            raise ApiError(str(exc), status_code=exc.status_code) from exc
        except ValueError as exc:
            raise ApiError(str(exc), status_code=400) from exc
        except RuntimeError as exc:
            raise ApiError(str(exc), status_code=503) from exc
        except Exception as exc:
            raise ApiError(f"Erreur Assistant IA: {exc}", status_code=502) from exc
        return result.model_dump(mode="json")


def _current_ai_runtime_config() -> AiRuntimeConfigOut:
    layer_providers = providers_by_layer()
    available_providers = {layer: values["available"] for layer, values in layer_providers.items()}
    disabled_providers = {layer: values["disabled"] for layer, values in layer_providers.items()}
    warnings: list[str] = []
    try:
        validate_layer_config("recommendation", ai_assistant_service.AI_PROVIDER, ai_assistant_service.AI_MODEL)
    except AIProviderConfigError as exc:
        warnings.append(f"recommendation: {exc}")
    return AiRuntimeConfigOut(
        AI_PROVIDER=ai_assistant_service.AI_PROVIDER,
        AI_MODEL=ai_assistant_service.AI_MODEL,
        AI_TEMPERATURE=ai_assistant_service.AI_TEMPERATURE,
        AI_TIMEOUT_SECONDS=int(app_config.AI_TIMEOUT_SECONDS),
        AI_ENABLE_BUSINESS_RULES=ai_assistant_service.AI_ENABLE_BUSINESS_RULES,
        AI_MAX_CANDIDATES=ai_assistant_service.MAX_CANDIDATES,
        AI_TARGET_INDICATORS=ai_assistant_service.TARGET_INDICATORS,
        WB_MAX_INDICATORS_PER_DATASET=app_config.SOURCE_LIMITS["WB"]["max_indicators_per_dataset"],
        message="Configuration IA runtime active.",
        providers_by_layer=available_providers,
        disabled_providers_by_layer=disabled_providers,
        models_by_layer=models_by_layer(),
        warnings=warnings,
    )


def get_ai_runtime_config() -> dict[str, Any]:
    return _current_ai_runtime_config().model_dump(mode="json")


def update_ai_runtime_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = AiRuntimeConfigIn.model_validate(payload)
    provider = config.AI_PROVIDER.strip().lower()
    try:
        validate_layer_config("recommendation", provider, config.AI_MODEL)
    except AIProviderConfigError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    ai_assistant_service.AI_PROVIDER = provider
    ai_assistant_service.AI_MODEL = config.AI_MODEL.strip() or "regles_metier_locales"
    ai_assistant_service.AI_TEMPERATURE = config.AI_TEMPERATURE
    app_config.AI_TIMEOUT_SECONDS = config.AI_TIMEOUT_SECONDS
    ai_assistant_service.AI_ENABLE_BUSINESS_RULES = config.AI_ENABLE_BUSINESS_RULES
    ai_assistant_service.MAX_CANDIDATES = config.AI_MAX_CANDIDATES
    ai_assistant_service.TARGET_INDICATORS = config.AI_TARGET_INDICATORS
    app_config.SOURCE_LIMITS["WB"]["max_indicators_per_dataset"] = config.WB_MAX_INDICATORS_PER_DATASET
    ai_business_rules.AI_MAX_CANDIDATES = config.AI_MAX_CANDIDATES
    current = _current_ai_runtime_config()
    current.message = "Configuration IA appliquée en mémoire. Elle sera perdue au redémarrage du service."
    return current.model_dump(mode="json")


def check_export_mode() -> dict:
    with _db_session() as db:
        return _check_export_mode(db)


def delete_export_dataset(slug: str) -> dict:
    with _db_session() as db:
        try:
            return _delete_export_dataset(db, slug)
        except PublishError as exc:
            raise _handle_publish_error(exc) from exc


def get_export_chronology_chart() -> tuple[bytes, str]:
    with _db_session() as db:
        try:
            png = build_export_chronology_png(db)
        except ModuleNotFoundError as exc:
            raise ApiError(
                "Le graphique nécessite matplotlib et seaborn.",
                status_code=503,
            ) from exc
        if png is None:
            raise ApiError("Aucun export.", status_code=404)
        return png, "image/png"


def get_dashboard_metrics() -> dict[str, Any]:
    with _db_session() as db:
        return build_dashboard_metrics(db)
