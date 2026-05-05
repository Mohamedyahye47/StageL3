from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import HF_DATASET_VISIBILITY, HF_NAMESPACE, hf_is_configured
from app.database import get_db
from app.schemas import (
    CountryOut,
    HfHealthOut,
    IndicatorOut,
    PublishDatasetIn,
    PublishResultOut,
    PublishedDatasetDataPreviewOut,
    PublishedDatasetDetailOut,
    PublishedDatasetListOut,
    PublishedDatasetVersionOut,
    SourceOut,
    TopicOut,
    AiRecommendationIn,
    DatasetBuildPreviewOut,
)

from app.services.ai_assistant_service import (
    ValidatedDatasetRecommendation,
    recommend_dataset_validated,
)

from app.services.catalog_service import (
    list_countries,
    list_indicators,
    list_sources,
    list_topics,
)
from app.services.hf_service import check_hf_health
from app.services.publish_service import (
    PublishError,
    delete_published_dataset,
    get_dashboard_datasets,
    get_dataset_detail,
    get_dataset_version_data_preview,
    list_dataset_versions,
    preview_dataset,
    publish_dataset,
    sync_datasets_with_huggingface,
)

api_router = APIRouter()


@api_router.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs", status_code=307)


@api_router.get("/api/sources", response_model=list[SourceOut], tags=["Catalogue"])
def api_list_sources(db: Session = Depends(get_db)):
    return list_sources(db)

@api_router.post(
    "/api/ai/recommend-dataset",
    response_model=ValidatedDatasetRecommendation,
    tags=["Assistant IA"],
)
def api_recommend_dataset(
    payload: AiRecommendationIn,
    db: Session = Depends(get_db),
):
    try:
        return recommend_dataset_validated(db, payload.user_request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Erreur Assistant IA: {exc}",
        ) from exc



@api_router.get("/api/topics", response_model=list[TopicOut], tags=["Catalogue"])
def api_list_topics(
    source_code: str | None = Query(default=None),
    source_id: int | None = Query(default=None),
    search: str = Query(default=""),
    db: Session = Depends(get_db),
):
    return list_topics(db, source_code=source_code, source_id=source_id, search=search)


@api_router.get("/api/indicators", response_model=list[IndicatorOut], tags=["Catalogue"])
def api_list_indicators(
    source_code: str | None = Query(default=None),
    topic_id: int | None = Query(default=None),
    search: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return list_indicators(db, source_code=source_code, topic_id=topic_id, search=search, limit=limit)


@api_router.get("/api/countries", response_model=list[CountryOut], tags=["Catalogue"])
def api_list_countries(
    search: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_countries(db, search=search, limit=limit)


@api_router.post("/api/publish-dataset", response_model=PublishResultOut, tags=["Publication"])
def api_publish_dataset(
    payload: PublishDatasetIn,
    db: Session = Depends(get_db),
):
    try:
        return publish_dataset(db, payload)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.post(
    "/api/datasets/preview",
    response_model=DatasetBuildPreviewOut,
    tags=["Publication"],
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
    "/api/published-datasets",
    response_model=list[PublishedDatasetListOut],
    tags=["Datasets publies"],
)
def api_list_published_datasets(db: Session = Depends(get_db)):
    return get_dashboard_datasets(db)


@api_router.get(
    "/api/published-datasets/{slug}",
    response_model=PublishedDatasetDetailOut,
    tags=["Datasets publies"],
)
def api_get_published_dataset(slug: str, db: Session = Depends(get_db)):
    detail = get_dataset_detail(db, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{slug}' introuvable.")
    return detail


@api_router.post(
    "/api/published-datasets/sync-hf",
    tags=["Datasets publies"],
)
def api_sync_published_datasets_with_hf(db: Session = Depends(get_db)):
    try:
        return sync_datasets_with_huggingface(db)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.delete(
    "/api/published-datasets/{slug}",
    tags=["Datasets publies"],
)
def api_delete_published_dataset(slug: str, db: Session = Depends(get_db)):
    try:
        return delete_published_dataset(db, slug)
    except PublishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@api_router.get(
    "/api/published-datasets/{slug}/versions",
    response_model=list[PublishedDatasetVersionOut],
    tags=["Datasets publies"],
)
def api_get_published_dataset_versions(slug: str, db: Session = Depends(get_db)):
    versions = list_dataset_versions(db, slug)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Dataset '{slug}' introuvable.")
    return versions


@api_router.get(
    "/api/published-datasets/{slug}/versions/{version}/data-preview",
    response_model=PublishedDatasetDataPreviewOut,
    tags=["Datasets publies"],
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


@api_router.get("/api/hf/health", response_model=HfHealthOut, tags=["Sante"])
def api_hf_health():
    if not hf_is_configured():
        return HfHealthOut(
            ok=False,
            namespace=HF_NAMESPACE or None,
            visibility=HF_DATASET_VISIBILITY,
            message="Configuration Hugging Face incomplete cote backend.",
        )
    return HfHealthOut(**check_hf_health())
