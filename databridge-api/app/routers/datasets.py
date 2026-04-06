# ============================================================
#  databridge-api/app/routers/datasets.py
#
#  Endpoints:
#    GET  /datasets              → paginated list of datasets
#    GET  /datasets/{id}         → dataset metadata + indicators
#    GET  /datasets/{id}/data    → real data from CSV file
# ============================================================

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models   import Dataset, DatasetIndicator, Indicator
from app.schemas  import DatasetOut, DatasetDetailOut, IndicatorOut
from app.utils.csv_reader import read_csv, count_csv_rows, csv_exists

router = APIRouter(prefix="/datasets", tags=["Datasets"])


# ============================================================
#  GET /datasets
# ============================================================

@router.get("/", response_model=List[DatasetOut])
def list_datasets(
    source_code : Optional[str] = Query(None, description="Filter by source code, e.g. IMF, WB, YAHOO"),
    frequency   : Optional[str] = Query(None, description="Filter by frequency: annual, monthly, quarterly"),
    limit       : int           = Query(20,   ge=1, le=200),
    offset      : int           = Query(0,    ge=0),
    db          : Session       = Depends(get_db),
):
    """
    Returns a paginated list of dataset metadata records.
    Optionally filter by source code or frequency.
    """
    from app.models import Source

    query = db.query(Dataset).filter(Dataset.status == "active")

    if source_code:
        src = db.query(Source).filter(Source.code == source_code.upper()).first()
        if not src:
            raise HTTPException(status_code=404, detail=f"Source '{source_code}' not found.")
        query = query.filter(Dataset.source_id == src.id)

    if frequency:
        query = query.filter(Dataset.frequency == frequency.lower())

    datasets = query.offset(offset).limit(limit).all()
    return datasets


# ============================================================
#  GET /datasets/{id}
# ============================================================

@router.get("/{dataset_id}", response_model=DatasetDetailOut)
def get_dataset(
    dataset_id : int,
    db         : Session = Depends(get_db),
):
    """
    Returns full metadata for one dataset, including its indicators
    and whether the CSV data file is available on disk.
    """
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail=f"Dataset id={dataset_id} not found.")

    # Build indicator list from the join table
    links = (
        db.query(DatasetIndicator)
        .filter(DatasetIndicator.dataset_id == dataset_id)
        .all()
    )
    indicator_ids = [link.indicator_id for link in links]
    indicators = (
        db.query(Indicator).filter(Indicator.id.in_(indicator_ids)).all()
        if indicator_ids else []
    )

    # Attach indicators to the dataset object for the response schema
    detail = DatasetDetailOut.model_validate(dataset)
    detail.indicators = [IndicatorOut.model_validate(i) for i in indicators]
    return detail


# ============================================================
#  GET /datasets/{id}/data
# ============================================================

@router.get("/{dataset_id}/data")
def get_dataset_data(
    dataset_id : int,
    limit      : int     = Query(100, ge=1,  le=5000, description="Max rows to return"),
    offset     : int     = Query(0,   ge=0,           description="Rows to skip"),
    db         : Session = Depends(get_db),
):
    """
    Returns the real data for a dataset, read directly from its CSV file.

    The CSV files are written by `run_all.py` to:
        data/{SOURCE_CODE}/{dataset_name}.csv

    Run the pipeline first if this returns 404.
    """
    from app.models import Source

    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail=f"Dataset id={dataset_id} not found.")

    source = db.query(Source).filter(Source.id == dataset.source_id).first()
    if not source:
        raise HTTPException(status_code=500, detail="Source record missing in DB.")

    if not csv_exists(source.code, dataset.name):
        raise HTTPException(
            status_code=404,
            detail=(
                f"CSV file not found for dataset '{dataset.name}'. "
                f"Run `python run_all.py` to generate data files."
            ),
        )

    total_rows = count_csv_rows(source.code, dataset.name)
    rows       = read_csv(source.code, dataset.name, limit=limit, offset=offset)

    return {
        "dataset_id"   : dataset_id,
        "dataset_name" : dataset.name,
        "source"       : source.code,
        "total_rows"   : total_rows,
        "returned_rows": len(rows),
        "offset"       : offset,
        "limit"        : limit,
        "data"         : rows,
    }
