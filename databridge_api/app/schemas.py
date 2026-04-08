# ============================================================
#  databridge-api/app/schemas.py
#
#  Pydantic v2 response models.
#  All fields match the SQLite schema — nothing extra added.
# ============================================================

from __future__ import annotations
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


# ============================================================
#  SOURCE
# ============================================================

class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id          : int
    name        : str
    code        : str
    base_url    : Optional[str] = None
    description : Optional[str] = None
    created_at  : Optional[str] = None


# ============================================================
#  INDICATOR
# ============================================================

class IndicatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id    : int
    code  : str
    label : str
    unit  : Optional[str] = None


# ============================================================
#  DATASET  (list view — no indicators)
# ============================================================

class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id             : int
    name           : str
    description    : Optional[str] = None
    source_id      : int
    format         : Optional[str] = None
    frequency      : Optional[str] = None
    ods_dataset_id : Optional[str] = None
    status         : Optional[str] = None
    created_at     : Optional[str] = None
    updated_at     : Optional[str] = None


# ============================================================
#  DATASET DETAIL  (single dataset — includes indicators)
# ============================================================

class DatasetDetailOut(DatasetOut):
    source     : SourceOut
    indicators : List[IndicatorOut] = []
