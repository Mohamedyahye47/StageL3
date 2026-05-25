from __future__ import annotations

import unicodedata

from django import template

register = template.Library()


def _normaliser_code(value: object) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.replace("-", "_").replace(" ", "_")


@register.filter
def status_label(value: object) -> str:
    labels = {
        "reussi": "Réussi",
        "success": "Succès",
        "succes": "Succès",
        "valide": "Valide",
        "valide_non_verifie": "Valide – aperçu non lancé",
        "verification_humaine_requise": "Vérification recommandée",
        "faible": "Résultat faible",
        "incoherent": "Incohérent",
        "unknown": "Non évalué",
        "erreur": "Erreur",
        "error": "Erreur",
        "echoue": "Erreur",
        "failed": "Erreur",
        "acceptable": "Acceptable",
        "weak": "Résultat faible",
        "export_links_ready": "Liens prêts",
        "draft": "Brouillon",
        "archived": "Archivé",
    }
    code = _normaliser_code(value)
    return labels.get(code, str(value or "Non évalué").replace("_", " "))


@register.filter
def status_badge_class(value: object) -> str:
    code = _normaliser_code(value)
    if code in {"reussi", "success", "succes", "valide", "acceptable", "export_links_ready"}:
        return "success"
    if code in {"valide_non_verifie"}:
        return "info"
    if code in {"verification_humaine_requise", "faible", "weak", "draft", "archived"}:
        return "warning"
    if code in {"incoherent", "erreur", "error", "echoue", "failed"}:
        return "danger"
    return "neutral"


@register.filter
def technical_label(value: object) -> str:
    labels = {
        "regles_metier_locales": "règles métier locales",
        "aucun_appel_ia": "aucun appel IA",
        "local_rules": "règles locales",
        "audit_only": "audit uniquement",
    }
    code = _normaliser_code(value)
    return labels.get(code, str(value or "-").replace("_", " "))
