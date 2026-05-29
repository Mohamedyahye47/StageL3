from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MEASURE_DIR = Path(os.getenv("DATABRIDGE_MEASURE_DIR", PROJECT_ROOT / "logs" / "mesures"))

MEASURE_FILES = {
    "base": "mesures_db.jsonl",
    "datasets": "mesures_datasets.jsonl",
    "ia": "mesures_ia.jsonl",
    "evaluation_ia": "ia_decisions.jsonl",
    "tests_ia": "ia_tests.jsonl",
    "wb_metadata_quality": "wb_metadata_quality.jsonl",
}


def demarrer_mesure() -> float:
    return time.perf_counter()


def terminer_mesure(debut: float) -> float:
    return round(time.perf_counter() - debut, 4)


def enregistrer_mesure(categorie: str, evenement: dict[str, Any]) -> Path:
    MEASURE_DIR.mkdir(parents=True, exist_ok=True)
    path = MEASURE_DIR / MEASURE_FILES.get(categorie, f"mesures_{categorie}.jsonl")
    cleaned_event = _nettoyer(evenement)
    if categorie in {"ia", "evaluation_ia", "tests_ia"}:
        cleaned_event = _with_ai_traceability(cleaned_event)
    payload = {
        "date": datetime.now(UTC).isoformat(),
        **cleaned_event,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return path


def lire_dernieres_mesures(categorie: str, limite: int = 50) -> list[dict[str, Any]]:
    path = MEASURE_DIR / MEASURE_FILES.get(categorie, f"mesures_{categorie}.jsonl")
    if not path.exists():
        return []
    lignes = path.read_text(encoding="utf-8").splitlines()
    evenements: list[dict[str, Any]] = []
    for ligne in lignes[-limite:]:
        try:
            evenements.append(json.loads(ligne))
        except ValueError:
            continue
    return evenements


def calculer_resume(evenements: list[dict[str, Any]], champ: str = "duree_totale_secondes") -> dict[str, Any]:
    valeurs = [
        float(evenement.get(champ))
        for evenement in evenements
        if evenement.get(champ) not in (None, "")
    ]
    if not valeurs:
        return {"nombre": 0, "moyenne": None, "minimum": None, "maximum": None}
    return {
        "nombre": len(valeurs),
        "moyenne": round(mean(valeurs), 4),
        "minimum": round(min(valeurs), 4),
        "maximum": round(max(valeurs), 4),
    }


def _nettoyer(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _nettoyer(item)
            for key, item in value.items()
            if not _est_secret(str(key))
        }
    if isinstance(value, list):
        return [_nettoyer(item) for item in value]
    return value


def _with_ai_traceability(evenement: dict[str, Any]) -> dict[str, Any]:
    payload = dict(evenement)
    payload.setdefault("source_execution", "unknown")
    payload.setdefault("triggered_by", "unknown")
    payload.setdefault("pipeline_version", "single_ai_v1")
    payload.setdefault("run_id", f"ia-{uuid.uuid4().hex}")
    return payload


def _est_secret(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in ("token", "api_key", "secret", "password"))
