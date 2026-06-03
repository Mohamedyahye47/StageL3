from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import config as app_config
from app.config import PUBLIC_API_BASE_URL, PUBLISH_MODE, REMOTE_PROVIDER, SOURCE_LIMITS, export_api_is_local
from app.ai.clients import AIProviderExecutionError
from app.ai.registry import AIProviderConfigError, models_by_layer, providers_by_layer, validate_layer_config
from app.database import get_db
from app.schemas import (
    AiRuntimeConfigIn,
    AiRuntimeConfigOut,
    AiRecommendationIn,
    CountryOut,
    DatasetBuildPreviewOut,
    ExportDatasetDataPreviewOut,
    ExportDatasetDetailOut,
    ExportDatasetListOut,
    ExportDatasetVersionOut,
    ExportLinksOut,
    IndicatorOut,
    OpenDataSoftMetadataOut,
    OpenDataSoftPublishOut,
    PublishDatasetIn,
    SourceOut,
    TopicOut,
)
from app.services import ai_assistant_service, ai_business_rules

from app.services.ai_assistant_service import (
    AIQuotaExceeded,
    ValidatedDatasetRecommendation,
    recommend_dataset_validated,
)
from app.services.model_evaluation_service import enregistrer_echec_decision_ia

from app.services.catalog_service import (
    list_countries,
    list_indicators,
    list_sources,
    list_topics,
)
from app.services.publish_service import (
    PublishError,
    check_export_mode,
    delete_export_dataset,
    export_dataset_csv,
    export_dataset_json,
    generate_export_links,
    get_dashboard_datasets,
    get_dataset_detail,
    get_dataset_version_data_preview,
    get_opendatasoft_metadata,
    list_dataset_versions,
    prepare_dataset_for_opendatasoft,
    preview_dataset,
    record_export_access,
)
from app.services.chart_service import build_export_chronology_png
from app.security import require_internal_token

api_router = APIRouter()


@api_router.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs", status_code=307)


@api_router.get("/api/sources", response_model=list[SourceOut], tags=["Catalogue"])
def api_list_sources(
    _: None = Depends(require_internal_token),
    db: Session = Depends(get_db),
):
    return list_sources(db)

@api_router.post(
    "/api/ai/recommend-dataset",
    response_model=ValidatedDatasetRecommendation,
    tags=["Assistant IA"],
)
def api_recommend_dataset(
    payload: AiRecommendationIn,
    audit: bool = Query(default=False),
    local_only: bool = Query(default=False),
    _: None = Depends(require_internal_token),
    db: Session = Depends(get_db),
):
    run_id = f"ia-{uuid.uuid4().hex}"
    try:
        return recommend_dataset_validated(
            db,
            payload.user_request,
            audit=audit,
            source_execution="assistant_ui",
            triggered_by="user_click",
            run_id=run_id,
            local_only=local_only,
        )
    except AIQuotaExceeded as exc:
        return JSONResponse(status_code=429, content=exc.to_payload())
    except ValueError as exc:
        enregistrer_echec_decision_ia(
            demande_utilisateur=payload.user_request,
            erreur=str(exc),
            source_execution="assistant_ui",
            triggered_by="user_click",
            run_id=run_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIProviderConfigError as exc:
        enregistrer_echec_decision_ia(
            demande_utilisateur=payload.user_request,
            erreur=str(exc),
            source_execution="assistant_ui",
            triggered_by="user_click",
            run_id=run_id,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIProviderExecutionError as exc:
        enregistrer_echec_decision_ia(
            demande_utilisateur=payload.user_request,
            erreur=str(exc),
            source_execution="assistant_ui",
            triggered_by="user_click",
            run_id=run_id,
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        enregistrer_echec_decision_ia(
            demande_utilisateur=payload.user_request,
            erreur=str(exc),
            source_execution="assistant_ui",
            triggered_by="user_click",
            run_id=run_id,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        enregistrer_echec_decision_ia(
            demande_utilisateur=payload.user_request,
            erreur=str(exc),
            source_execution="assistant_ui",
            triggered_by="user_click",
            run_id=run_id,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Erreur Assistant IA: {exc}",
        ) from exc


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
        WB_MAX_INDICATORS_PER_DATASET=SOURCE_LIMITS["WB"]["max_indicators_per_dataset"],
        message="Configuration IA runtime active.",
        providers_by_layer=available_providers,
        disabled_providers_by_layer=disabled_providers,
        models_by_layer=models_by_layer(),
        warnings=warnings,
    )


@api_router.get("/api/ai/runtime-config", response_model=AiRuntimeConfigOut, tags=["Assistant IA"])
def api_get_ai_runtime_config(_: None = Depends(require_internal_token)):
    return _current_ai_runtime_config()


@api_router.post("/api/ai/runtime-config", response_model=AiRuntimeConfigOut, tags=["Assistant IA"])
def api_update_ai_runtime_config(
    payload: AiRuntimeConfigIn,
    _: None = Depends(require_internal_token),
):
    provider = payload.AI_PROVIDER.strip().lower()
    try:
        validate_layer_config("recommendation", provider, payload.AI_MODEL)
    except AIProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if provider == "openai_compatible" and not payload.AI_MODEL.strip():
        raise HTTPException(status_code=400, detail="Les modèles IA sont obligatoires.")
    ai_assistant_service.AI_PROVIDER = provider
    ai_assistant_service.AI_MODEL = payload.AI_MODEL.strip() or "regles_metier_locales"
    ai_assistant_service.AI_TEMPERATURE = payload.AI_TEMPERATURE
    app_config.AI_TIMEOUT_SECONDS = payload.AI_TIMEOUT_SECONDS
    ai_assistant_service.AI_ENABLE_BUSINESS_RULES = payload.AI_ENABLE_BUSINESS_RULES
    ai_assistant_service.MAX_CANDIDATES = payload.AI_MAX_CANDIDATES
    ai_assistant_service.TARGET_INDICATORS = payload.AI_TARGET_INDICATORS

    app_config.SOURCE_LIMITS["WB"]["max_indicators_per_dataset"] = payload.WB_MAX_INDICATORS_PER_DATASET
    SOURCE_LIMITS["WB"]["max_indicators_per_dataset"] = payload.WB_MAX_INDICATORS_PER_DATASET
    ai_business_rules.AI_MAX_CANDIDATES = payload.AI_MAX_CANDIDATES

    current = _current_ai_runtime_config()
    current.message = "Configuration IA appliquée en mémoire. Elle sera perdue au redémarrage du serveur FastAPI."
    return current



@api_router.get(
    "/api/topics",
    response_model=list[TopicOut],
    tags=["Catalogue"],
    dependencies=[Depends(require_internal_token)],
)
def api_list_topics(
    source_code: str | None = Query(default=None),
    source_id: int | None = Query(default=None),
    search: str = Query(default=""),
    db: Session = Depends(get_db),
):
    return list_topics(db, source_code=source_code, source_id=source_id, search=search)


@api_router.get(
    "/api/indicators",
    response_model=list[IndicatorOut],
    tags=["Catalogue"],
    dependencies=[Depends(require_internal_token)],
)
def api_list_indicators(
    source_code: str | None = Query(default=None),
    topic_id: int | None = Query(default=None),
    search: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return list_indicators(
        db,
        source_code=source_code,
        topic_id=topic_id,
        search=search,
        limit=limit,
        offset=offset,
    )


@api_router.get("/api/source-limits", tags=["Catalogue"], dependencies=[Depends(require_internal_token)])
def api_source_limits():
    return {
        code: {
            "max_indicators_per_dataset": int(values["max_indicators_per_dataset"]),
            "label": values["label"],
        }
        for code, values in SOURCE_LIMITS.items()
    }


@api_router.get(
    "/api/countries",
    response_model=list[CountryOut],
    tags=["Catalogue"],
    dependencies=[Depends(require_internal_token)],
)
def api_list_countries(
    search: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_countries(db, search=search, limit=limit)


@api_router.post("/api/publish-dataset", tags=["Compatibilite"], dependencies=[Depends(require_internal_token)])
def api_publish_dataset(
    payload: PublishDatasetIn,
    db: Session = Depends(get_db),
):
    raise HTTPException(
        status_code=410,
        detail="Cette ancienne route est desactivee. Utilisez la generation de liens d'export.",
    )


@api_router.post(
    "/api/datasets/generate-export-links",
    response_model=ExportLinksOut,
    tags=["Exports"],
    dependencies=[Depends(require_internal_token)],
)
def api_generate_export_links(
    payload: PublishDatasetIn,
    db: Session = Depends(get_db),
):
    try:
        return generate_export_links(db, payload)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.post(
    "/api/datasets/preview",
    response_model=DatasetBuildPreviewOut,
    tags=["Exports"],
    dependencies=[Depends(require_internal_token)],
)
def api_preview_dataset(
    payload: PublishDatasetIn,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    try:
        return preview_dataset(db, payload, limit=limit)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.get(
    "/api/opendata/exports/{slug}.csv",
    tags=["Opendata"],
)
def api_export_dataset_csv(
    slug: str,
    request: Request,
    token: str | None = Query(default=None),
    download: bool = Query(default=False),
    preview: bool = Query(default=False),
    view: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    try:
        csv_text = export_dataset_csv(db, slug, token)
    except PublishError as exc:
        record_export_access(
            db,
            slug=slug,
            export_format="csv",
            request=request,
            status="refused",
            error_message=str(exc),
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    record_export_access(
        db,
        slug=slug,
        export_format="csv",
        request=request,
        status="success",
    )

    disposition_type = "attachment" if download else "inline"

    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition_type}; filename="{slug}.csv"',
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        },
    )


@api_router.get(
    "/api/opendata/exports/{slug}.json",
    tags=["Opendata"],
)
def api_export_dataset_json(
    slug: str,
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        rows = export_dataset_json(db, slug, token)
    except PublishError as exc:
        record_export_access(db, slug=slug, export_format="json", request=request, status="refused", error_message=str(exc))
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    record_export_access(db, slug=slug, export_format="json", request=request, status="success")
    return rows


@api_router.get(
    "/api/export-datasets",
    response_model=list[ExportDatasetListOut],
    tags=["Exports"],
    dependencies=[Depends(require_internal_token)],
)
def api_list_export_datasets(db: Session = Depends(get_db)):
    return get_dashboard_datasets(db)


@api_router.get(
    "/api/export-datasets/{slug}",
    response_model=ExportDatasetDetailOut,
    tags=["Exports"],
    dependencies=[Depends(require_internal_token)],
)
def api_get_export_dataset(slug: str, db: Session = Depends(get_db)):
    detail = get_dataset_detail(db, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{slug}' introuvable.")
    return detail


@api_router.get(
    "/api/export-datasets/{slug}/opendatasoft-metadata",
    response_model=OpenDataSoftMetadataOut,
    tags=["OpenDataSoft"],
    dependencies=[Depends(require_internal_token)],
)
def api_get_opendatasoft_metadata(slug: str, db: Session = Depends(get_db)):
    try:
        return get_opendatasoft_metadata(db, slug)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.post(
    "/api/export-datasets/{slug}/prepare-opendatasoft",
    response_model=OpenDataSoftPublishOut,
    tags=["OpenDataSoft"],
    dependencies=[Depends(require_internal_token)],
)
def api_prepare_opendatasoft(slug: str, db: Session = Depends(get_db)):
    try:
        return prepare_dataset_for_opendatasoft(db, slug)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.post(
    "/api/export-datasets/check-mode",
    tags=["Exports"],
    dependencies=[Depends(require_internal_token)],
)
def api_check_export_mode(db: Session = Depends(get_db)):
    return check_export_mode(db)


@api_router.delete(
    "/api/export-datasets/{slug}",
    tags=["Exports"],
    dependencies=[Depends(require_internal_token)],
)
def api_delete_export_dataset(slug: str, db: Session = Depends(get_db)):
    try:
        return delete_export_dataset(db, slug)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.get(
    "/api/export-datasets/{slug}/versions",
    response_model=list[ExportDatasetVersionOut],
    tags=["Exports"],
    dependencies=[Depends(require_internal_token)],
)
def api_get_export_dataset_versions(slug: str, db: Session = Depends(get_db)):
    versions = list_dataset_versions(db, slug)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Dataset '{slug}' introuvable.")
    return versions


@api_router.get(
    "/api/export-datasets/{slug}/versions/{version}/data-preview",
    response_model=ExportDatasetDataPreviewOut,
    tags=["Exports"],
    dependencies=[Depends(require_internal_token)],
)
def api_get_dataset_data_preview(
    slug: str,
    version: int,
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    try:
        preview = get_dataset_version_data_preview(db, slug, version, limit=limit)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if preview is None:
        raise HTTPException(status_code=404, detail=f"Version '{version}' introuvable pour '{slug}'.")
    return preview


@api_router.get("/api/export/charts/chronology.png", tags=["Exports"], dependencies=[Depends(require_internal_token)])
def api_export_chronology_chart(db: Session = Depends(get_db)):
    try:
        png = build_export_chronology_png(db)
    except ModuleNotFoundError as exc:
        if exc.name in {"matplotlib", "seaborn"}:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Le graphique nécessite matplotlib et seaborn. "
                    "Installez les dépendances du serveur FastAPI avec "
                    "'python -m pip install -r requirements.txt'."
                ),
            ) from exc
        raise
    if png is None:
        raise HTTPException(status_code=404, detail="Aucun export.")
    return Response(content=png, media_type="image/png")


@api_router.get("/api/export/health", tags=["Sante"], dependencies=[Depends(require_internal_token)])
def api_export_health():
    return {
        "ok": True,
        "provider": REMOTE_PROVIDER,
        "export_mode": PUBLISH_MODE,
        "public_api_base_url": PUBLIC_API_BASE_URL,
        "is_local_url": export_api_is_local(),
        "opendatasoft_link_mode": True,
        "source_limits": SOURCE_LIMITS,
        "message": "API d'export active. Generez un lien CSV puis utilisez-le comme ressource HTTP/URL dans Opendatasoft.",
    }
