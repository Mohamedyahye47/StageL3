from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Country, Indicator, Source, Topic


def list_sources(db: Session) -> list[Source]:
    return db.scalars(select(Source).order_by(Source.name.asc())).all()


def list_topics(
    db: Session,
    *,
    source_code: str | None = None,
    source_id: int | None = None,
    search: str = "",
) -> list[Topic]:
    query = select(Topic).join(Source)
    if source_code:
        query = query.where(Source.code == source_code.upper())
    if source_id:
        query = query.where(Topic.source_id == source_id)
    if search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Topic.name.ilike(pattern),
                Topic.description.ilike(pattern),
            )
        )
    return db.scalars(query.order_by(Topic.name.asc())).all()


def list_indicators(
    db: Session,
    *,
    source_code: str | None = None,
    topic_id: int | None = None,
    search: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[Indicator]:
    query = (
        select(Indicator)
        .options(selectinload(Indicator.topic_links))
        .join(Source)
    )
    if source_code:
        query = query.where(Source.code == source_code.upper())
    if topic_id:
        query = query.where(Indicator.topic_links.any(topic_id=topic_id))
    if search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Indicator.code.ilike(pattern),
                Indicator.name.ilike(pattern),
                Indicator.description.ilike(pattern),
            )
        )
    return db.scalars(
        query.order_by(Indicator.code.asc()).offset(max(0, offset)).limit(limit)
    ).all()


def list_countries(
    db: Session,
    *,
    search: str = "",
    limit: int = 30,
) -> list[Country]:
    query = select(Country).where(Country.enabled.is_(True))
    if search.strip():
        pattern = f"%{search.strip()}%"
        upper_search = search.strip().upper()
        query = query.where(
            or_(
                Country.name.ilike(pattern),
                Country.code_iso3.ilike(pattern),
                Country.code_iso2.ilike(pattern),
                Country.wb_code.ilike(pattern),
                Country.code_iso3 == upper_search,
                Country.code_iso2 == upper_search,
                Country.wb_code == upper_search,
            )
        )
    return db.scalars(query.order_by(Country.name.asc()).limit(limit)).all()
