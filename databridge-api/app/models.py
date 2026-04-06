# ============================================================
#  databridge-api/app/models.py
#
#  SQLAlchemy ORM models — REFLECT the existing SQLite schema.
#  DO NOT call Base.metadata.create_all().
#  DO NOT alter column definitions.
# ============================================================

from sqlalchemy import Column, Integer, String, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Source(Base):
    __tablename__ = "sources"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String,  nullable=False)
    code        = Column(String,  unique=True, nullable=False)
    base_url    = Column(Text)
    description = Column(Text)
    created_at  = Column(String)

    datasets    = relationship("Dataset",   back_populates="source")
    topics      = relationship("Topic",     back_populates="source")
    indicators  = relationship("Indicator", back_populates="source")


class Topic(Base):
    __tablename__ = "topics"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String,  nullable=False)
    source_id   = Column(Integer, ForeignKey("sources.id"), nullable=False)
    description = Column(Text)

    source      = relationship("Source", back_populates="topics")
    indicators  = relationship("Indicator", back_populates="topic")


class Indicator(Base):
    __tablename__ = "indicators"

    id        = Column(Integer, primary_key=True, index=True)
    code      = Column(String,  nullable=False)
    label     = Column(String,  nullable=False)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    topic_id  = Column(Integer, ForeignKey("topics.id"))
    unit      = Column(String)

    source    = relationship("Source", back_populates="indicators")
    topic     = relationship("Topic",  back_populates="indicators")
    dataset_links = relationship("DatasetIndicator", back_populates="indicator")


class Dataset(Base):
    __tablename__ = "datasets"

    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String,  unique=True, nullable=False)
    description    = Column(Text)
    source_id      = Column(Integer, ForeignKey("sources.id"), nullable=False)
    format         = Column(String)
    frequency      = Column(String)
    ods_dataset_id = Column(String)
    status         = Column(String,  default="active")
    created_at     = Column(String)
    updated_at     = Column(String)

    source         = relationship("Source",           back_populates="datasets")
    indicator_links = relationship("DatasetIndicator", back_populates="dataset")


class DatasetIndicator(Base):
    __tablename__ = "dataset_indicators"

    dataset_id   = Column(Integer, ForeignKey("datasets.id"),   primary_key=True)
    indicator_id = Column(Integer, ForeignKey("indicators.id"), primary_key=True)

    dataset      = relationship("Dataset",   back_populates="indicator_links")
    indicator    = relationship("Indicator", back_populates="dataset_links")
