# ============================================================
#  databridge-api/app/database.py
#
#  SQLAlchemy engine wired to the existing databridge.db.
#  READ-ONLY: we never call create_all() or modify the schema.
# ============================================================

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Path to the SQLite file — go up two levels from this file
# (databridge-api/app/ → databridge-api/ → project root)
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))

DB_PATH   = os.getenv("DB_PATH", os.path.join(_PROJECT_ROOT, "databridge.db"))
DATA_DIR  = os.getenv("DATA_DIR", os.path.join(_PROJECT_ROOT, "data"))

DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # needed for SQLite + FastAPI
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


# ── Dependency injected into routers ──────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
