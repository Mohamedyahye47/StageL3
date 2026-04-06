# ============================================================
#  databridge-api/app/main.py
#
#  Entry point for the Richat DataBridge API.
#
#  Start with:
#      cd databridge-api
#      uvicorn app.main:app --reload
#
#  Docs available at:
#      http://127.0.0.1:8000/docs   (Swagger UI)
#      http://127.0.0.1:8000/redoc  (ReDoc)
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import datasets, sources

# ── App ────────────────────────────────────────────────────
app = FastAPI(
    title       = "Richat DataBridge API",
    description = (
        "Read-only REST API for Mauritania economic datasets.\n\n"
        "**Metadata** is read from `databridge.db` (SQLite).\n"
        "**Real data** is read from CSV files under `data/`.\n\n"
        "Run `python run_all.py` to populate data files before querying `/data` endpoints."
    ),
    version     = "1.0.0",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# ── CORS (open for local dev) ──────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["GET"],
    allow_headers     = ["*"],
)

# ── Routers ────────────────────────────────────────────────
app.include_router(datasets.router)
app.include_router(sources.router)


# ── Health check ───────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {
        "service" : "Richat DataBridge API",
        "version" : "1.0.0",
        "status"  : "running",
        "docs"    : "/docs",
    }
