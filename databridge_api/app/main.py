from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
for path in (API_ROOT, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core import db as core_db

from app.api import api_router
from app.config import API_TITLE, CORS_ALLOWED_ORIGINS, IS_PRODUCTION


def _resolve_cors_origins() -> list[str]:
    if CORS_ALLOWED_ORIGINS:
        return CORS_ALLOWED_ORIGINS
    if IS_PRODUCTION:
        raise RuntimeError("CORS_ALLOWED_ORIGINS est obligatoire en production.")
    return ["http://127.0.0.1:8000", "http://localhost:8000"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize core metadata tables and create any missing export tables.
    core_db.init_db()
    core_db.seed_metadata(force=False)
    yield


app = FastAPI(
    title=API_TITLE,
    description=(
        "API metadata-only de Richat DataBridge. "
        "La creation de dataset reste ephemere jusqu'a la generation des liens d'export. "
        "Le backend expose des endpoints CSV/JSON stables consommables par Richat Opendatasoft. "
        "Le workflow actif repose uniquement sur l'API d'export Opendatasoft."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router)
