from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
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
    version_links: Mapped[list["PublishedDatasetVersionIndicator"]] = relationship(back_populates="indicator")

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

    versions: Mapped[list["PublishedDatasetVersion"]] = relationship(back_populates="country")


class PublishedDataset(Base):
    __tablename__ = "published_datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    remote_provider: Mapped[str] = mapped_column(String, nullable=False)
    remote_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    visibility: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    versions: Mapped[list["PublishedDatasetVersion"]] = relationship(
        back_populates="dataset",
        order_by="PublishedDatasetVersion.version",
    )
    publish_logs: Mapped[list["PublishLog"]] = relationship(back_populates="dataset")


class PublishedDatasetVersion(Base):
    __tablename__ = "published_dataset_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("published_datasets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    remote_version: Mapped[str] = mapped_column(String, nullable=False)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id"), nullable=False)
    start_date: Mapped[str] = mapped_column(String, nullable=False)
    end_date: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False)
    frequency: Mapped[str] = mapped_column(String, nullable=False)
    manifest_url: Mapped[str] = mapped_column(Text, nullable=False)
    build_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[str] = mapped_column(String, nullable=False)

    dataset: Mapped[PublishedDataset] = relationship(back_populates="versions")
    country: Mapped[Country] = relationship(back_populates="versions")
    indicator_links: Mapped[list["PublishedDatasetVersionIndicator"]] = relationship(back_populates="dataset_version")
    publish_logs: Mapped[list["PublishLog"]] = relationship(back_populates="version")


class PublishedDatasetVersionIndicator(Base):
    __tablename__ = "published_dataset_version_indicators"

    dataset_version_id: Mapped[int] = mapped_column(
        ForeignKey("published_dataset_versions.id"),
        primary_key=True,
    )
    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id"), primary_key=True)

    dataset_version: Mapped[PublishedDatasetVersion] = relationship(back_populates="indicator_links")
    indicator: Mapped[Indicator] = relationship(back_populates="version_links")


class PublishLog(Base):
    __tablename__ = "publish_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("published_datasets.id"))
    version_id: Mapped[int | None] = mapped_column(ForeignKey("published_dataset_versions.id"))
    remote_provider: Mapped[str] = mapped_column(String, nullable=False)
    remote_id: Mapped[str | None] = mapped_column(String)
    remote_version: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    dataset: Mapped[PublishedDataset | None] = relationship(back_populates="publish_logs")
    version: Mapped[PublishedDatasetVersion | None] = relationship(back_populates="publish_logs")
