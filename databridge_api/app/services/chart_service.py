from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExportDataset, ExportLog


# ── Seuils d'affichage ────────────────────────────────────────────────────────
# Faciles à ajuster selon le volume réel de données.
MIN_POINTS_SCATTER = 5       # En-dessous : message "données insuffisantes"
MIN_POINTS_MARGINAL = 10     # En-dessous : scatter simple sans distributions marginales
MIN_POINTS_JOINTPLOT = 30    # En-dessous : scatter + histogrammes simples
MIN_POINTS_KDE = 50          # En-dessous : histogrammes ; au-dessus : courbes KDE

# ── Palette ───────────────────────────────────────────────────────────────────
_PALETTE = {
    "Réussi": "#2563eb",
    "Erreur": "#dc2626",
    "Inconnu": "#64748b",
}
_COLOR_JOINT = "#2563eb"
_COLOR_MARG_X = "#2563eb"
_COLOR_MARG_Y = "#14b8a6"
_ALPHA_HIST = 0.28
_ALPHA_KDE = 0.35
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

    x = [r["ordre"] for r in records]
    y = [r["lignes"] for r in records]
    etats = [r["etat"] for r in records]

    if n < MIN_POINTS_MARGINAL:
        return _construire_scatter_simple(x, y, etats, n)

    if n < MIN_POINTS_KDE:
        return _construire_scatter_histogrammes(x, y, etats, n)

    return _construire_jointplot_kde(x, y, etats, n)


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
        })

    return records


# ── Modes de rendu ────────────────────────────────────────────────────────────

def _construire_scatter_simple(
    x: list[int],
    y: list[int],
    etats: list[str],
    n: int,
) -> bytes:
    """
    5 – 9 exports : nuage de points seul, sans distributions marginales.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="whitegrid", context="notebook", font_scale=0.95)

    fig, ax = plt.subplots(figsize=_FIGSIZE, constrained_layout=True)
    fig.patch.set_facecolor("white")

    data = {"ordre": x, "lignes": y, "etat": etats}
    sns.scatterplot(
        data=data, x="ordre", y="lignes", hue="etat",
        palette=_PALETTE, s=90, edgecolor="white", linewidth=0.8,
        alpha=0.92, ax=ax,
    )

    _annoter_points(ax, x, y)
    _formater_axes_joint(ax)
    _titre_avec_mode(ax, n, "scatter simple")

    legend = ax.get_legend()
    if legend:
        legend.set_title("État")
        legend.get_frame().set_alpha(0.9)

    sns.despine(ax=ax)
    return _sauvegarder(fig)


def _construire_scatter_histogrammes(
    x: list[int],
    y: list[int],
    etats: list[str],
    n: int,
) -> bytes:
    """
    10 – 49 exports : nuage de points + histogrammes marginaux (grille jointplot).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="whitegrid", context="notebook", font_scale=0.95)

    fig, ax_joint, ax_marg_x, ax_marg_y = _creer_grille(figsize=_FIGSIZE)
    fig.patch.set_facecolor("white")

    data = {"ordre": x, "lignes": y, "etat": etats}
    sns.scatterplot(
        data=data, x="ordre", y="lignes", hue="etat",
        palette=_PALETTE, s=80, edgecolor="white", linewidth=0.7,
        alpha=0.92, ax=ax_joint,
    )

    bins_x = _calcul_bins(x)
    bins_y = _calcul_bins(y)

    ax_marg_x.hist(x, bins=bins_x, color=_COLOR_MARG_X, alpha=_ALPHA_HIST, edgecolor="#bfdbfe")
    ax_marg_y.hist(y, bins=bins_y, orientation="horizontal", color=_COLOR_MARG_Y, alpha=_ALPHA_HIST, edgecolor="#99f6e4")

    _formater_axes_joint(ax_joint)
    _formater_axes_marginaux(ax_marg_x, ax_marg_y)
    _titre_avec_mode(ax_joint, n, "scatter + histogrammes")

    legend = ax_joint.get_legend()
    if legend:
        legend.set_title("État")
        legend.get_frame().set_alpha(0.9)

    sns.despine(fig=fig)
    return _sauvegarder(fig)


def _construire_jointplot_kde(
    x: list[int],
    y: list[int],
    etats: list[str],
    n: int,
) -> bytes:
    """
    50+ exports : nuage de points + courbes KDE marginales.
    Rendu le plus riche, réservé aux volumes suffisants.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="whitegrid", context="notebook", font_scale=0.95)

    fig, ax_joint, ax_marg_x, ax_marg_y = _creer_grille(figsize=_FIGSIZE)
    fig.patch.set_facecolor("white")

    data = {"ordre": x, "lignes": y, "etat": etats}
    sns.scatterplot(
        data=data, x="ordre", y="lignes", hue="etat",
        palette=_PALETTE, s=72, edgecolor="white", linewidth=0.7,
        alpha=0.85, ax=ax_joint,
    )

    # KDE marginales (pleine densité)
    sns.kdeplot(x=x, ax=ax_marg_x, color=_COLOR_MARG_X, fill=True, alpha=_ALPHA_KDE, linewidth=1.2)
    sns.kdeplot(y=y, ax=ax_marg_y, color=_COLOR_MARG_Y, fill=True, alpha=_ALPHA_KDE, linewidth=1.2, vertical=True)

    _formater_axes_joint(ax_joint)
    _formater_axes_marginaux(ax_marg_x, ax_marg_y)
    _titre_avec_mode(ax_joint, n, "jointplot KDE")

    legend = ax_joint.get_legend()
    if legend:
        legend.set_title("État")
        legend.get_frame().set_alpha(0.9)

    sns.despine(fig=fig)
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


# ── Grille jointplot ──────────────────────────────────────────────────────────

def _creer_grille(figsize: tuple[float, float]):
    """
    Crée une grille 2×2 imitant le layout de seaborn JointGrid :
    - ax_marg_x : distribution en haut
    - ax_joint  : nuage de points au centre-bas
    - ax_marg_y : distribution à droite
    """
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=figsize, constrained_layout=True)
    grid = fig.add_gridspec(
        2, 2,
        width_ratios=(4, 1.2),
        height_ratios=(1.1, 4),
        hspace=0.06,
        wspace=0.06,
    )
    ax_marg_x = fig.add_subplot(grid[0, 0])
    ax_joint = fig.add_subplot(grid[1, 0], sharex=ax_marg_x)
    ax_marg_y = fig.add_subplot(grid[1, 1], sharey=ax_joint)

    return fig, ax_joint, ax_marg_x, ax_marg_y


# ── Helpers visuels ───────────────────────────────────────────────────────────

def _formater_axes_joint(ax) -> None:
    ax.set_xlabel("Ordre chronologique", fontsize=10)
    ax.set_ylabel("Lignes générées", fontsize=10)


def _formater_axes_marginaux(ax_marg_x, ax_marg_y) -> None:
    ax_marg_x.set_ylabel("Fréquence", fontsize=8)
    ax_marg_x.set_xlabel("")
    ax_marg_x.tick_params(labelbottom=False, labelsize=7)
    ax_marg_y.set_xlabel("Fréquence", fontsize=8)
    ax_marg_y.set_ylabel("")
    ax_marg_y.tick_params(labelleft=False, labelsize=7)


def _titre_avec_mode(ax, n: int, mode: str) -> None:
    """Affiche le titre principal et un sous-titre discret indiquant le mode."""
    ax.set_title("Chronologie des exports", fontsize=13, fontweight="bold", pad=12)
    ax.annotate(
        f"{n} export(s) · {mode}",
        xy=(0.99, 0.01), xycoords="axes fraction",
        ha="right", va="bottom", fontsize=7.5, color="#94a3b8",
    )


def _annoter_points(ax, x: list, y: list) -> None:
    """Pour les petits jeux de données, annote chaque point avec son index."""
    for xi, yi in zip(x, y):
        ax.annotate(
            str(xi),
            xy=(xi, yi),
            xytext=(4, 4), textcoords="offset points",
            fontsize=7.5, color="#475569",
        )


def _calcul_bins(values: list) -> int | list[float]:
    if not values:
        return 1
    unique = set(values)
    if len(unique) == 1:
        v = float(list(unique)[0])
        return [v - 0.5, v + 0.5]
    return min(10, max(3, len(values) // 2))


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