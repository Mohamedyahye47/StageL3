# ============================================================
#  databridge-api/app/routers/sources.py
#
#  Endpoints:
#    GET /sources        → list all sources
#    GET /sources/{id}   → single source detail
# ============================================================

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models   import Source
from app.schemas  import SourceOut

router = APIRouter(prefix="/sources", tags=["Sources"])


@router.get("/", response_model=List[SourceOut])
def list_sources(db: Session = Depends(get_db)):
    """Returns all registered data sources."""
    return db.query(Source).order_by(Source.id).all()


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int, db: Session = Depends(get_db)):
    """Returns metadata for a single source."""
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail=f"Source id={source_id} not found.")
    return source
