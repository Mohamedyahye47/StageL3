from __future__ import annotations

from typing import Any

from app.models import Country, Indicator, Source, Topic
from app.services.measure_service import enregistrer_mesure, lire_dernieres_mesures


def enregistrer_journal_decision_ia_detaille(journal: dict[str, Any]) -> dict[str, Any]:
    """Persist the v2 AI decision journal without exposing secrets."""
    enregistrer_mesure("evaluation_ia", journal)
    return journal


def enregistrer_journal_decision_ia(
    *,
    demande_utilisateur: str,
    analyse_ia: Any,
    termes_expanses: list[str],
    candidats: list[Indicator],
    candidats_envoyes: list[dict[str, Any]],
    selection_brute: list[Any],
    indicateurs_acceptes: list[Indicator],
    indicateurs_rejetes: list[dict[str, Any]],
    source: Source | None,
    pays: Country | None,
    theme: Topic | None,
    date_debut: str,
    date_fin: str,
    etapes: dict[str, Any],
    duree_totale_secondes: float,
    limite_source: int,
    confiance: str,
) -> dict[str, Any]:
    """Persist a safe, readable decision log for AI recommendations."""
    termes_lower = {str(term).lower() for term in termes_expanses}
    regles = _regles_appliquees(termes_lower, limite_source)
    points_forts = _points_forts(
        source=source,
        pays=pays,
        theme=theme,
        candidats=candidats,
        indicateurs_acceptes=indicateurs_acceptes,
        date_debut=date_debut,
        date_fin=date_fin,
        limite_source=limite_source,
        confiance=confiance,
    )
    points_faibles = _points_faibles(
        demande_utilisateur=demande_utilisateur,
        theme=theme,
        candidats=candidats,
        indicateurs_acceptes=indicateurs_acceptes,
        indicateurs_rejetes=indicateurs_rejetes,
        confiance=confiance,
    )

    journal = {
        "type": "evaluation_assistant_ia",
        "etat": "Réussi" if indicateurs_acceptes else "À vérifier",
        "demande_utilisateur": demande_utilisateur,
        "analyse_ia": {
            "source": getattr(analyse_ia, "source_code", None),
            "pays": getattr(analyse_ia, "country_name", None),
            "annee_debut": getattr(analyse_ia, "start_year", None),
            "annee_fin": getattr(analyse_ia, "end_year", None),
            "mots_cles": list(getattr(analyse_ia, "search_keywords", []) or []),
            "confiance": getattr(analyse_ia, "confidence", None),
        },
        "recherche_locale": {
            "termes_expanses": termes_expanses,
            "nombre_candidats": len(candidats),
            "candidats_principaux": [
                {
                    "id": indicator.id,
                    "code": indicator.code,
                    "nom": indicator.name,
                }
                for indicator in candidats[:10]
            ],
            "nombre_candidats_envoyes": len(candidats_envoyes),
        },
        "regles_metier": regles,
        "selection_finale": {
            "acceptes": [
                {
                    "id": indicator.id,
                    "code": indicator.code,
                    "nom": indicator.name,
                }
                for indicator in indicateurs_acceptes
            ],
            "rejetes": indicateurs_rejetes,
            "selection_brute": [
                {
                    "id": getattr(item, "id", None),
                    "raison": getattr(item, "reason", ""),
                }
                for item in selection_brute
            ],
        },
        "validation": {
            "source_valide": source is not None,
            "pays_valide": pays is not None,
            "theme_valide": theme is not None,
            "theme_final": theme.name if theme else None,
            "pays_final": pays.name if pays else None,
            "source_finale": source.code if source else None,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "indicateurs_valides": len(indicateurs_acceptes),
            "limite_source": limite_source,
            "dans_limite_source": len(indicateurs_acceptes) <= limite_source,
        },
        "points_forts": points_forts,
        "points_faibles": points_faibles,
        "etapes": etapes,
        "duree_totale_secondes": duree_totale_secondes,
    }
    enregistrer_mesure("evaluation_ia", journal)
    return journal


def lire_dernieres_evaluations_ia(limite: int = 50) -> list[dict[str, Any]]:
    return lire_dernieres_mesures("evaluation_ia", limite=limite)


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
        "pipeline_version": "ai_chain_v2",
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


def _regles_appliquees(termes_lower: set[str], limite_source: int) -> list[str]:
    regles = [
        "Analyse IA sans invention de codes indicateurs",
        "Expansion par vocabulaire contrôlé",
        "Recherche obligatoire dans la base locale",
        "Sélection limitée aux candidats locaux",
        "Validation finale par le serveur",
        f"Plafond source : {limite_source} indicateurs",
    ]
    if {"population", "habitants", "individus"} & termes_lower:
        regles.append("Priorité population")
    if {"inflation", "prix", "cpi"} & termes_lower:
        regles.append("Priorité inflation")
    if {"pib", "gdp", "croissance"} & termes_lower:
        regles.append("Priorité croissance économique")
    return regles


def _points_forts(
    *,
    source: Source | None,
    pays: Country | None,
    theme: Topic | None,
    candidats: list[Indicator],
    indicateurs_acceptes: list[Indicator],
    date_debut: str,
    date_fin: str,
    limite_source: int,
    confiance: str,
) -> list[str]:
    points: list[str] = []
    if source is not None:
        points.append(f"La source {source.code} existe dans la base locale.")
    if pays is not None:
        points.append(f"Le pays {pays.name} a été validé dans la base locale.")
    if theme is not None:
        points.append(f"Le thème final est validé : {theme.name}.")
    if candidats:
        points.append(f"{len(candidats)} candidat(s) locaux ont été trouvés avant la sélection.")
    if indicateurs_acceptes:
        points.append(f"{len(indicateurs_acceptes)} indicateur(s) accepté(s) existent dans la base locale.")
    if len(indicateurs_acceptes) <= limite_source:
        points.append("La sélection respecte la limite maximale de la source.")
    if date_debut and date_fin:
        points.append(f"La période a été normalisée : {date_debut} à {date_fin}.")
    return points or ["Aucun point fort automatique détecté."]


def _points_faibles(
    *,
    demande_utilisateur: str,
    theme: Topic | None,
    candidats: list[Indicator],
    indicateurs_acceptes: list[Indicator],
    indicateurs_rejetes: list[dict[str, Any]],
    confiance: str,
) -> list[str]:
    points: list[str] = []
    lowered_request = demande_utilisateur.lower()
    if "individus" in lowered_request and "population" not in lowered_request:
        points.append("La formulation utilise 'individus', qui doit être rapproché du vocabulaire population.")
    if confiance == "low":
        points.append("La confiance de l'analyse IA est faible.")
    if len(candidats) < 5:
        points.append("Peu de candidats locaux ont été trouvés pour cette demande.")
    if not indicateurs_acceptes:
        points.append("Aucun indicateur n'a été accepté après validation serveur.")
    if indicateurs_rejetes:
        points.append(f"{len(indicateurs_rejetes)} indicateur(s) ont été rejetés par les règles serveur.")
    if theme is None:
        points.append("Aucun thème dominant n'a été trouvé automatiquement.")
    return points or ["Aucun point faible automatique détecté."]
