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
from app.config import API_TITLE
from app.database import Base, engine

# Import all models so SQLAlchemy registers them with Base.metadata before create_all
import app.models  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize core metadata tables and create any missing publication tables.
    core_db.init_db()
    core_db.seed_metadata(force=False)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=API_TITLE,
    description=(
        "API metadata-only de Richat DataBridge. "
        "La creation de dataset reste ephemere jusqu'a la publication. "
        "Apres succes de publication vers Hugging Face, le miroir SQLite local est mis a jour. "
        "Les valeurs publiees restent dans les fichiers distants, le miroir SQLite conserve les metadonnees."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
