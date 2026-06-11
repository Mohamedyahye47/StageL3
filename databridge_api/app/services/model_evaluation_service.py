from __future__ import annotations

from typing import Any

from app.services.measure_service import enregistrer_mesure


def enregistrer_journal_decision_ia_detaille(journal: dict[str, Any]) -> dict[str, Any]:
    """Persist the v2 AI decision journal without exposing secrets."""
    enregistrer_mesure("evaluation_ia", journal)
    return journal


def enregistrer_echec_decision_ia(
    *,
    demande_utilisateur: str,
    erreur: str,
    source_execution: str = "unknown",
    triggered_by: str = "unknown",
    run_id: str | None = None,
) -> dict[str, Any]:
    journal = {
        "type": "evaluation_assistant_ia",
        "source_execution": source_execution,
        "triggered_by": triggered_by,
        "pipeline_version": "single_ai_v1",
        "run_id": run_id,
        "etat": "Erreur",
        "demande_utilisateur": demande_utilisateur,
        "analyse_ia": {},
        "recherche_locale": {
            "termes_expanses": [],
            "nombre_candidats": 0,
            "candidats_principaux": [],
            "nombre_candidats_envoyes": 0,
        },
        "regles_metier": ["Échec enregistré sans exposer de secret"],
        "selection_finale": {
            "acceptes": [],
            "rejetes": [],
            "selection_brute": [],
        },
        "validation": {
            "source_valide": False,
            "pays_valide": False,
            "theme_valide": False,
            "donnees_disponibles": False,
        },
        "points_forts": ["L'échec a été journalisé pour analyse."],
        "points_faibles": [erreur],
        "etapes": {},
        "duree_totale_secondes": None,
    }
    enregistrer_mesure("evaluation_ia", journal)
    return journal


