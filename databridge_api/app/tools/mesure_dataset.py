from __future__ import annotations

import argparse

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Country, Indicator, Source
from app.schemas import PublishDatasetIn
from app.services.publish_service import preview_dataset


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mesure la construction reelle d'un dataset sans publication.")
    parser.add_argument("--country", default="MRT", help="Code pays Banque mondiale, par exemple MRT.")
    parser.add_argument("--indicators", type=int, default=5, help="Nombre d'indicateurs a tester.")
    parser.add_argument("--start", type=int, default=2000, help="Annee de debut.")
    parser.add_argument("--end", type=int, default=2023, help="Annee de fin.")
    parser.add_argument("--source", default="WB", help="Code source.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    with SessionLocal() as db:
        source = db.scalar(select(Source).where(Source.code == args.source.upper()))
        if source is None:
            raise SystemExit(f"Source introuvable : {args.source}")

        country = db.scalar(
            select(Country).where(
                Country.enabled.is_(True),
                Country.wb_code == args.country.upper(),
            )
        )
        if country is None:
            raise SystemExit(f"Pays introuvable : {args.country}")

        indicators = db.scalars(
            select(Indicator)
            .where(Indicator.source_id == source.id)
            .order_by(Indicator.code.asc())
            .limit(max(1, args.indicators))
        ).all()
        if not indicators:
            raise SystemExit("Aucun indicateur disponible pour cette source.")

        payload = PublishDatasetIn(
            source_code=source.code,
            topic_id=None,
            indicator_ids=[indicator.id for indicator in indicators],
            country_id=country.id,
            start_date=f"{args.start}-01-01",
            end_date=f"{args.end}-12-31",
            title=f"Mesure dataset {country.name} {len(indicators)} indicateurs",
            description="Mesure locale sans publication ni generation de liens.",
            existing_slug=None,
            format="csv",
        )
        result = preview_dataset(db, payload, limit=1)

    print("Mesure dataset terminee")
    print(f"Pays : {country.name}")
    print(f"Indicateurs : {len(indicators)}")
    print(f"Periode : {args.start}-{args.end}")
    print(f"Lignes reelles : {result['row_count']}")
    print(f"Valeurs non nulles : {result['non_null_value_count']}")
    print("La mesure detaillee est enregistree dans logs/mesures/mesures_datasets.jsonl")


if __name__ == "__main__":
    main()
