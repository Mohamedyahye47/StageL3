from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    topics: Mapped[list["Topic"]] = relationship(back_populates="source")
    indicators: Mapped[list["Indicator"]] = relationship(back_populates="source")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(back_populates="topics")
    indicator_links: Mapped[list["IndicatorTopic"]] = relationship(back_populates="topic")


class Indicator(Base):
    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    periodicity: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    source: Mapped[Source] = relationship(back_populates="indicators")
    topic_links: Mapped[list["IndicatorTopic"]] = relationship(back_populates="indicator")
    export_links: Mapped[list["ExportDatasetIndicator"]] = relationship(back_populates="indicator")

    @property
    def topic_ids(self) -> list[int]:
        return [link.topic_id for link in self.topic_links]


class IndicatorTopic(Base):
    __tablename__ = "indicator_topics"

    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id"), primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), primary_key=True)

    indicator: Mapped[Indicator] = relationship(back_populates="topic_links")
    topic: Mapped[Topic] = relationship(back_populates="indicator_links")


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_iso3: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    code_iso2: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    wb_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    exports: Mapped[list["ExportDataset"]] = relationship(back_populates="country")


class ExportDataset(Base):
    __tablename__ = "export_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id"), nullable=False)
    start_date: Mapped[str] = mapped_column(String, nullable=False)
    end_date: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False, default="opendatasoft_url")
    csv_export_url: Mapped[str] = mapped_column(Text, nullable=False)
    json_export_url: Mapped[str] = mapped_column(Text, nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    format: Mapped[str] = mapped_column(String, nullable=False, default="csv")
    frequency: Mapped[str] = mapped_column(String, nullable=False, default="non precisee")
    build_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    source: Mapped[Source] = relationship()
    topic: Mapped[Topic] = relationship()
    country: Mapped[Country] = relationship(back_populates="exports")
    indicator_links: Mapped[list["ExportDatasetIndicator"]] = relationship(back_populates="export_dataset")
    export_logs: Mapped[list["ExportLog"]] = relationship(back_populates="export_dataset")


class ExportDatasetIndicator(Base):
    __tablename__ = "export_dataset_indicators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    export_dataset_id: Mapped[int] = mapped_column(ForeignKey("export_datasets.id"), nullable=False)
    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id"), nullable=False)

    export_dataset: Mapped[ExportDataset] = relationship(back_populates="indicator_links")
    indicator: Mapped[Indicator] = relationship(back_populates="export_links")


class ExportLog(Base):
    __tablename__ = "export_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    export_dataset_id: Mapped[int | None] = mapped_column(ForeignKey("export_datasets.id"))
    action: Mapped[str] = mapped_column(String, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    non_null_value_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    export_dataset: Mapped[ExportDataset | None] = relationship(back_populates="export_logs")
