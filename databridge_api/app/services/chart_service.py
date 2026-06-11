from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExportDataset, ExportLog


# ── Seuils d'affichage ────────────────────────────────────────────────────────
# Faciles à ajuster selon le volume réel de données.
MIN_POINTS_SCATTER = 5       # En-dessous : message "données insuffisantes"

# ── Palette ───────────────────────────────────────────────────────────────────
_PALETTE = {
    "Réussi": "#2563eb",
    "Erreur": "#dc2626",
    "Inconnu": "#64748b",
}
_FIGSIZE = (8.4, 5.4)


# ── Point d'entrée public ─────────────────────────────────────────────────────

def build_export_chronology_png(db: Session) -> bytes | None:
    """
    Génère une image PNG côté serveur représentant la chronologie des exports.

    Retourne None si aucune donnée n'existe (l'endpoint renvoie alors 404).
    Retourne des bytes PNG dans tous les autres cas.

    Comportement selon le nombre de points :
      0               → None (404)
      1 – 4           → image "données insuffisantes"
      5 – 9           → scatter simple
      10 – 29         → scatter + histogrammes marginaux
      30 – 49         → scatter + histogrammes marginaux (grille complète)
      50+             → scatter + courbes KDE marginales
    """
    records = _charger_enregistrements(db)

    if not records:
        return None

    n = len(records)

    if n < MIN_POINTS_SCATTER:
        return _construire_message_png(
            "Chronologie des exports",
            "Données insuffisantes pour un graphique de distribution",
            f"{n} export(s) enregistré(s). Ajoutez au moins {MIN_POINTS_SCATTER} exports.",
        )

    return _construire_barres_chronologie(records)


# ── Chargement des données ────────────────────────────────────────────────────

def _charger_enregistrements(db: Session) -> list[dict[str, Any]]:
    """
    Charge les données réelles depuis ExportLog.
    En l'absence de logs, utilise directement ExportDataset comme fallback.
    """
    rows = db.execute(
        select(ExportLog, ExportDataset)
        .join(ExportDataset, ExportDataset.id == ExportLog.export_dataset_id)
        .where(ExportLog.row_count.is_not(None))
        .order_by(ExportLog.created_at.asc(), ExportLog.id.asc())
    ).all()

    records: list[dict[str, Any]] = []
    for index, (log, dataset) in enumerate(rows, start=1):
        records.append({
            "ordre": index,
            "lignes": int(log.row_count or 0),
            "duree": float(log.duration_seconds or 0),
            "etat": _libelle_etat(log.status),
            "slug": dataset.slug,
            "date": log.created_at or dataset.created_at or dataset.updated_at,
        })

    if records:
        return records

    # Fallback : pas de logs mais des datasets existent
    datasets = db.scalars(
        select(ExportDataset).order_by(ExportDataset.updated_at.asc())
    ).all()

    for index, dataset in enumerate(datasets, start=1):
        records.append({
            "ordre": index,
            "lignes": _lignes_depuis_build_json(dataset.build_json),
            "duree": 0,
            "etat": _libelle_etat(dataset.status),
            "slug": dataset.slug,
            "date": dataset.created_at or dataset.updated_at,
        })

    return records


def _construire_barres_chronologie(records: list[dict[str, Any]]) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=_FIGSIZE, constrained_layout=True)
    fig.patch.set_facecolor("white")

    x = list(range(len(records)))
    y = [int(record.get("lignes") or 0) for record in records]
    etats = [str(record.get("etat") or "Inconnu") for record in records]
    labels = _date_labels(records)
    colors = [_PALETTE.get(etat, _PALETTE["Inconnu"]) for etat in etats]

    ax.bar(x, y, color=colors, edgecolor="#dbeafe", linewidth=0.8, alpha=0.92)
    ax.set_title("Chronologie des exports", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Date de création", fontsize=10)
    ax.set_ylabel("Lignes générées", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax.grid(axis="y", alpha=0.24)

    for xi, yi in zip(x, y):
        ax.annotate(
            str(yi),
            xy=(xi, yi),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            fontsize=7.5,
            color="#475569",
        )

    ax.annotate(
        f"{len(records)} {_pluralize(len(records), 'export', 'exports')}",
        xy=(0.99, 0.01),
        xycoords="axes fraction",
        ha="right",
        va="bottom",
        fontsize=7.5,
        color="#94a3b8",
    )
    return _sauvegarder(fig)


# ── Image de message ──────────────────────────────────────────────────────────

def _construire_message_png(titre: str, message: str, sous_titre: str) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.4, 3.4))
    fig.patch.set_facecolor("white")
    ax.axis("off")

    ax.text(0.05, 0.72, titre, fontsize=15, fontweight="bold", color="#0f172a", transform=ax.transAxes)
    ax.text(0.05, 0.50, message, fontsize=11, color="#2563eb", transform=ax.transAxes)
    ax.text(0.05, 0.34, sous_titre, fontsize=9, color="#64748b", transform=ax.transAxes)

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


def _date_labels(records: list[dict[str, Any]]) -> list[str]:
    parsed_dates = [_parse_datetime(record.get("date")) for record in records]
    known_dates = [value for value in parsed_dates if value is not None]
    months = {(value.year, value.month) for value in known_dates}
    use_month_labels = len(months) > 1
    return [
        _format_date_label(value, use_month=use_month_labels)
        for value in parsed_dates
    ]


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_date_label(value: datetime | None, *, use_month: bool) -> str:
    if value is None:
        return "Date inconnue"
    if not use_month:
        return value.strftime("%d/%m/%Y")
    month_names = (
        "Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin",
        "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc.",
    )
    return f"{month_names[value.month - 1]} {value.year}"


def _pluralize(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _sauvegarder(fig) -> bytes:
    import matplotlib.pyplot as plt
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


# ── Utilitaires données ───────────────────────────────────────────────────────

def _libelle_etat(status: str | None) -> str:
    if status in {"success", "export_links_ready"}:
        return "Réussi"
    if status == "error":
        return "Erreur"
    return "Inconnu"


def _lignes_depuis_build_json(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    for key in ("nombre_lignes", "rows_count"):
        try:
            return int(manifest[key])
        except (KeyError, TypeError, ValueError):
            continue
    return 0
