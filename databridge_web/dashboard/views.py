from __future__ import annotations

from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import re
from typing import Any
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


from dashboard.services.api_client import get_ai_dataset_recommendation
from django.contrib.auth import get_user_model, login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .audit import record_audit_event
from .forms import DatasetCreateForm, SuperuserCreationForm, SuperuserPasswordResetForm
from .services import api_client
from .services.api_client import ApiError, BackendUnavailable
from django.conf import settings

DEFAULT_SOURCE_CODE = "WB"
DEFAULT_START_DATE = date(2020, 1, 1).isoformat()
DEFAULT_END_DATE = date(2024, 12, 31).isoformat()
HIDDEN_PREVIEW_COLUMNS = {"unite", "statut_observation", "decimales"}
DEFAULT_SOURCE_LIMITS = {
    "WB": {
        "max_indicators_per_dataset": 60,
        "label": "Banque mondiale",
    }
}
DATASET_PAGE_SIZE = 6
INDICATOR_PAGE_SIZE = 7
MEASURE_PAGE_SIZE = 7
NUMERIC_MEASURE_FIELDS = {
    "nombre_themes",
    "nombre_pays",
    "nombre_indicateurs",
    "nombre_appels_http",
    "nombre_lignes",
    "nombre_lignes_theorique",
    "valeurs_non_nulles",
    "annee_debut",
    "annee_fin",
    "duree_totale_secondes",
    "duree_moyenne_par_indicateur",
    "nombre_candidats",
    "nombre_candidats_apres_regles",
    "nombre_candidats_envoyes",
    "limite_candidats_ia",
    "nombre_appels_ia",
    "nombre_indicateurs_bloques",
    "nombre_indicateurs_selectionnes",
    "nombre_indicateurs_valides",
}
MODEL_PARAMETER_GROUPS = [
    {
        "title": "Assistant IA",
        "eyebrow": "Appel unique",
        "description": (
            "Un seul appel IA reçoit la demande utilisateur et les candidats locaux préparés par le serveur. "
            "La réponse reste validée par la base locale avant d'être proposée au builder."
        ),
        "fields": [
            {"name": "AI_PROVIDER", "label": "Fournisseur IA", "type": "select", "layer": "recommendation"},
            {"name": "AI_MODEL", "label": "Modèle IA", "type": "select", "layer": "recommendation", "provider_field": "AI_PROVIDER"},
            {"name": "AI_TEMPERATURE", "label": "Température", "type": "range", "min": 0, "max": 1, "step": 0.1, "value_type": "float"},
            {"name": "AI_TARGET_INDICATORS", "label": "Nombre cible d'indicateurs", "type": "range", "min": 1, "max": 20, "step": 1},
        ],
    },
    {
        "title": "Garde-fous locaux",
        "eyebrow": "Règles métier serveur",
        "description": (
            "Couche non IA : validation de la source, du pays, du thème, de l'existence des indicateurs "
            "et des limites de sécurité."
        ),
        "fields": [
            {"name": "AI_ENABLE_BUSINESS_RULES", "label": "Activer les règles métier", "type": "boolean"},
            {"name": "AI_MAX_CANDIDATES", "label": "Candidats maximum transmis à l'IA", "type": "range", "min": 10, "max": 100, "step": 5},
            {"name": "WB_MAX_INDICATORS_PER_DATASET", "label": "Limite indicateurs Banque mondiale", "type": "range", "min": 1, "max": 60, "step": 1},
        ],
    },
]


def login_view(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect("dashboard:dashboard")

    form = AuthenticationForm(request, data=request.POST or None)
    next_url = request.GET.get("next") or request.POST.get("next") or reverse("dashboard:dashboard")

    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        if not user.is_superuser:
            record_audit_event(
                request,
                action="login_refused_non_superuser",
                object_type="user",
                object_id=user.get_username(),
                user=user,
            )
            form.add_error(None, "Acces reserve aux superusers autorises.")
        else:
            auth_login(request, user)
            record_audit_event(
                request,
                action="login_success",
                object_type="user",
                object_id=user.get_username(),
                user=user,
            )
            safe_next = next_url if next_url.startswith("/") and not next_url.startswith("//") else reverse("dashboard:dashboard")
            return redirect(safe_next)

    return render(
        request,
        "login.html",
        {
            "form": form,
            "next": next_url,
        },
    )


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        record_audit_event(
            request,
            action="logout",
            object_type="user",
            object_id=request.user.get_username(),
        )
    auth_logout(request)
    messages.success(request, "Deconnexion effectuee.")
    return redirect("dashboard:login")


def first_admin_setup(request):
    User = get_user_model()
    if User.objects.filter(is_superuser=True).exists():
        return redirect("dashboard:dashboard" if request.user.is_authenticated else "dashboard:login")

    form = SuperuserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        record_audit_event(
            request,
            action="first_admin_created",
            object_type="user",
            object_id=user.get_username(),
            user=user,
        )
        auth_login(request, user)
        messages.success(request, "Premier administrateur cree. Bienvenue dans Richat DataBridge.")
        return redirect("dashboard:dashboard")

    return render(request, "first_admin.html", {"form": form})


def superuser_management(request):
    User = get_user_model()
    create_form = SuperuserCreationForm()
    password_form = None

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "create":
            create_form = SuperuserCreationForm(request.POST)
            if create_form.is_valid():
                user = create_form.save()
                record_audit_event(
                    request,
                    action="superuser_created",
                    object_type="user",
                    object_id=user.get_username(),
                    extra={"created_user_id": user.id},
                )
                messages.success(request, f"Administrateur cree : {user.get_username()}.")
                return redirect("dashboard:superuser_management")

        elif action == "disable":
            target = _get_superuser_or_none(request.POST.get("user_id"))
            if target is None:
                messages.error(request, "Administrateur introuvable.")
            elif target == request.user:
                messages.error(request, "Vous ne pouvez pas desactiver votre propre session depuis cette page.")
            elif User.objects.filter(is_superuser=True, is_active=True).exclude(id=target.id).count() == 0:
                messages.error(request, "Impossible de desactiver le dernier superuser actif.")
            else:
                target.is_active = False
                target.save(update_fields=["is_active"])
                record_audit_event(
                    request,
                    action="superuser_disabled",
                    object_type="user",
                    object_id=target.get_username(),
                    extra={"target_user_id": target.id},
                )
                messages.success(request, f"Administrateur desactive : {target.get_username()}.")
                return redirect("dashboard:superuser_management")

        elif action == "reset_password":
            target = _get_superuser_or_none(request.POST.get("user_id"))
            if target is None:
                messages.error(request, "Administrateur introuvable.")
            else:
                password_form = SuperuserPasswordResetForm(request.POST, target_user=target)
                if password_form.is_valid():
                    password_form.save()
                    if target == request.user:
                        update_session_auth_hash(request, target)
                    record_audit_event(
                        request,
                        action="superuser_password_changed",
                        object_type="user",
                        object_id=target.get_username(),
                        extra={"target_user_id": target.id},
                    )
                    messages.success(request, f"Mot de passe mis a jour pour : {target.get_username()}.")
                    return redirect("dashboard:superuser_management")
                messages.error(request, "Le nouveau mot de passe ne respecte pas les règles de validation.")

    users = User.objects.filter(is_superuser=True).order_by("username")
    return render(
        request,
        "users.html",
        {
            "active_page": "users",
            "users": users,
            "create_form": create_form,
            "password_form": password_form,
        },
    )


def healthz(request):
    return JsonResponse({"ok": True})


def export_chronology_chart(request):
    try:
        content, content_type = api_client.get_export_chronology_chart()
    except (BackendUnavailable, ApiError):
        return HttpResponse(status=503)
    return HttpResponse(content, content_type=content_type or "image/png")


def _get_superuser_or_none(user_id):
    wanted = _clean_int(user_id)
    if wanted is None:
        return None
    User = get_user_model()
    return User.objects.filter(id=wanted, is_superuser=True).first()


def _build_preview_table(preview: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    columns = [
        column
        for column in preview.get("columns", [])
        if str(column).lower() not in HIDDEN_PREVIEW_COLUMNS
    ]
    rows = [
        [row.get(column, "") for column in columns]
        for row in preview.get("rows", [])
    ]
    return columns, rows


def _form_error_message(form: DatasetCreateForm) -> str:
    for field_name in form.errors:
        errors = form.errors.get(field_name)
        if errors:
            return str(errors[0])
    return "Vérifiez les champs du formulaire avant de lancer l'aperçu."


def _build_builder_prefill(recommendation: dict[str, Any]) -> dict[str, Any]:
    indicators = recommendation.get("indicators") or []
    return {
        "source_code": recommendation.get("source_code") or DEFAULT_SOURCE_CODE,
        "source_id": recommendation.get("source_id"),
        "topic_id": recommendation.get("topic_id"),
        "topic_name": recommendation.get("topic_name") or "",
        "country_id": recommendation.get("country_id"),
        "country_name": recommendation.get("country_name") or "",
        "country_code": recommendation.get("country_code") or "",
        "start_date": recommendation.get("start_date") or DEFAULT_START_DATE,
        "end_date": recommendation.get("end_date") or DEFAULT_END_DATE,
        "title": recommendation.get("title") or "",
        "description": recommendation.get("description") or "",
        "indicator_ids": [
            int(item["id"])
            for item in indicators
            if _clean_int(item.get("id")) is not None
        ],
        "indicator_codes": [
            str(item.get("code", "")).strip()
            for item in indicators
            if str(item.get("code", "")).strip()
        ],
        "indicator_details": [
            {
                "id": item.get("id"),
                "code": item.get("code", ""),
                "name": item.get("name", ""),
            }
            for item in indicators
            if item.get("id") or item.get("code")
        ],
    }


def _build_light_ai_recommendation(recommendation: dict[str, Any]) -> dict[str, Any]:
    indicators = recommendation.get("indicators") or []
    return {
        "source_code": recommendation.get("source_code") or DEFAULT_SOURCE_CODE,
        "topic_name": recommendation.get("topic_name") or "",
        "country_name": recommendation.get("country_name") or "",
        "country_code": recommendation.get("country_code") or "",
        "start_date": recommendation.get("start_date") or DEFAULT_START_DATE,
        "end_date": recommendation.get("end_date") or DEFAULT_END_DATE,
        "title": recommendation.get("title") or "",
        "description": recommendation.get("description") or "",
        "confidence": recommendation.get("confidence") or "",
        "etat_technique": recommendation.get("etat_technique") or "",
        "etat_metier": recommendation.get("etat_metier") or "",
        "ai_calls": recommendation.get("ai_calls", 0),
        "source_execution": recommendation.get("source_execution") or "",
        "correction_detectee": recommendation.get("correction_detectee") or "",
        "country_resolution": recommendation.get("country_resolution") or {},
        "fournisseur_ia": recommendation.get("fournisseur_ia") or ("local" if recommendation.get("source_execution") == "local_rules" else ""),
        "modele_ia": recommendation.get("modele_ia") or ("règles métier locales" if recommendation.get("source_execution") == "local_rules" else ""),
        "tokens_utilises": recommendation.get("tokens_utilises"),
        "tokens_restants": recommendation.get("tokens_restants"),
        "quota_requetes_atteint": recommendation.get("quota_requetes_atteint", False),
        "retry_after_seconds": recommendation.get("retry_after_seconds"),
        "missing_indicator_codes": recommendation.get("missing_indicator_codes") or [],
        "indicators": [
            {
                "id": item.get("id"),
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "reason": item.get("reason", ""),
            }
            for item in indicators
            if item.get("id") or item.get("code")
        ],
    }


def _looks_like_html_error(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return (
        "<!doctype html" in lowered
        or "<html" in lowered
        or "<title>502" in lowered
        or "application loading" in lowered
        or "service waking up" in lowered
        or ("render.com" in lowered and "<style" in lowered)
        or "bad gateway" in lowered
    )


def _safe_backend_error(exc_or_text: Exception | str | None) -> str | None:
    if exc_or_text is None:
        return None

    text = str(exc_or_text).strip()

    if not text:
        return None

    lowered = text.lower()

    if _looks_like_html_error(text):
        return (
            "Serveur FastAPI temporairement indisponible ou en réveil Render. "
            "Réessayez après quelques secondes."
        )

    if "127.0.0.1" in lowered or "localhost" in lowered:
        return (
            "Configuration invalide : une URL locale 127.0.0.1/localhost est encore utilisée. "
            "En production, utilisez https://databridge-api.onrender.com."
        )

    if "connection refused" in lowered or "failed to establish a new connection" in lowered:
        return "FastAPI est indisponible pour le moment. Vérifiez le service databridge-api."

    if "read timed out" in lowered or "timeout" in lowered:
        return "FastAPI met trop de temps à répondre. Réessayez après le réveil du service."

    if "403" in lowered or "accès refusé" in lowered or "acces refuse" in lowered:
        return "Accès refusé par FastAPI. Vérifiez que INTERNAL_API_TOKEN est identique dans Django et FastAPI."

    if len(text) > 500:
        return text[:500] + "..."

    return text


def _public_api_base_url() -> str:
    return str(getattr(settings, "FASTAPI_BASE_URL", "")).rstrip("/")


def _normalise_public_export_url(url: str | None) -> str | None:
    if not url:
        return url

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    if hostname not in {"127.0.0.1", "localhost"}:
        return url

    public_base = _public_api_base_url()

    if not public_base:
        return url

    public_parsed = urlparse(public_base)

    return urlunparse(
        parsed._replace(
            scheme=public_parsed.scheme,
            netloc=public_parsed.netloc,
        )
    )


def _normalise_export_links(links: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(links, dict):
        return links

    cleaned = dict(links)

    for key in ("csv_url", "json_url", "data_url", "url", "download_url"):
        if key in cleaned:
            cleaned[key] = _normalise_public_export_url(cleaned.get(key))

    return cleaned



def _assistant_error_message(exc: Exception) -> str:
    if isinstance(exc, BackendUnavailable):
        return (
            "API FastAPI indisponible temporairement. "
            "Ouvrez https://databridge-api.onrender.com/healthz pour réveiller le backend, puis réessayez."
        )

    if isinstance(exc, ApiError):
        payload_text = json.dumps(exc.payload, ensure_ascii=False) if exc.payload else ""
        message_text = f"{exc} {payload_text}".lower()

        if _looks_like_html_error(message_text):
            return (
                "FastAPI a retourné une page Render/HTML au lieu d'une réponse JSON. "
                "Le backend est probablement en réveil ou indisponible."
            )

        if exc.status_code == 400:
            if any(term in message_text for term in ("0 candidat", "aucun candidat", "no candidate", "local")):
                if "mots-clés" in message_text or "mots-cles" in message_text or "codes world bank" in message_text:
                    return str(exc)
                return (
                    "Aucun indicateur local correspondant n’a été trouvé. "
                    "Pour ce thème, ajoutez des règles locales ou utilisez une demande plus précise."
                )

            if "mode local" in message_text or "local" in message_text:
                return (
                    "Le mode local ne reconnaît pas encore cette demande. "
                    "Ajoutez l’intention dans les règles métier locales."
                )

            return (
                "La demande n’a pas pu être traitée. "
                "Vérifiez la formulation ou utilisez une requête plus précise."
            )

        if exc.status_code == 403:
            return (
                "FastAPI refuse la requête. Vérifiez que INTERNAL_API_TOKEN est identique "
                "dans databridge-web et databridge-api."
            )

        if exc.status_code == 429:
            return "Quota IA atteint. Réessayez plus tard ou utilisez le mode local."

        if exc.status_code in {502, 503, 504} or any(
            term in message_text
            for term in ("unavailable", "overloaded", "high demand", "surcharge", "503", "502", "504")
        ):
            return "Le service IA/FastAPI est temporairement indisponible. Réessayez après quelques secondes."

        if "disabled" in message_text or "désactiv" in message_text:
            return "Assistant IA désactivé dans la configuration."

    safe_error = _safe_backend_error(exc)
    return safe_error or "La recommandation n’a pas pu être générée. Utilisez le mode règles locales ou reformulez la demande."

def ai_assistant(request):
    recommendation = None
    error = None
    user_request = ""

    if request.method == "POST":
        action = request.POST.get("action", "generate")

        if action in {"generate", "generate_local"}:
            user_request = request.POST.get("user_request", "").strip()

            if not user_request:
                error = "Veuillez décrire votre besoin d'analyse."
            else:
                try:
                    recommendation = get_ai_dataset_recommendation(
                        user_request,
                        local_only=action == "generate_local",
                    )

                    light_recommendation = _build_light_ai_recommendation(recommendation)

                    request.session["ai_recommendation"] = light_recommendation
                    request.session["ai_user_request"] = user_request
                    request.session.modified = True

                    record_audit_event(
                        request,
                        action="assistant_ia_launched",
                        object_type="assistant_ia",
                        object_id=recommendation.get("run_id", ""),
                        extra={
                            "local_only": action == "generate_local",
                            "source_execution": recommendation.get("source_execution"),
                            "status": "success",
                        },
                    )

                    if recommendation.get("source_execution") == "local_rules":
                        messages.info(request, "Proposition locale construite sans appel au fournisseur IA.")

                except ApiError as exc:
                    if exc.status_code == 429 and exc.payload.get("error_type") == "ai_quota_exceeded":
                        retry_after = exc.payload.get("retry_after_seconds")
                        if retry_after:
                            error = f"Quota IA atteint. Réessayez dans environ {retry_after} secondes ou utilisez la proposition locale."
                        else:
                            error = "Quota IA atteint. Réessayez plus tard ou utilisez la proposition locale."
                    else:
                        error = _assistant_error_message(exc)

                    record_audit_event(
                        request,
                        action="assistant_ia_failed",
                        object_type="assistant_ia",
                        extra={
                            "status_code": exc.status_code,
                            "local_only": action == "generate_local",
                        },
                    )

                except BackendUnavailable as exc:
                    error = _assistant_error_message(exc)
                    record_audit_event(
                        request,
                        action="assistant_ia_failed",
                        object_type="assistant_ia",
                        extra={
                            "reason": "backend_unavailable",
                            "local_only": action == "generate_local",
                        },
                    )

                except Exception as exc:
                    error = _assistant_error_message(exc)
                    record_audit_event(
                        request,
                        action="assistant_ia_failed",
                        object_type="assistant_ia",
                        extra={
                            "reason": exc.__class__.__name__,
                            "local_only": action == "generate_local",
                        },
                    )

        elif action in {"use_in_builder", "apply"}:
            prefill = None

            if request.session.get("ai_recommendation"):
                prefill = _build_builder_prefill(request.session["ai_recommendation"])
                request.session["builder_prefill"] = prefill
                request.session.modified = True

            if not prefill:
                error = "Aucune proposition IA disponible. Générez d'abord une proposition."
            elif not prefill.get("indicator_ids") and not prefill.get("indicator_codes"):
                error = "Aucun indicateur valide dans la proposition."
            else:
                return redirect("dashboard:dataset_create")

    else:
        request.session.pop("ai_recommendation", None)
        request.session.pop("ai_user_request", None)
        request.session.pop("builder_prefill", None)
        request.session.modified = True

    return render(
        request,
        "ai_assistant.html",
        {
            "active_page": "assistant",
            "user_request": user_request,
            "recommendation": recommendation,
            "error": error,
            "quota_error": bool(error and "Quota IA atteint" in error),
        },
    )



def dashboard(request):
    datasets, backend_error = _load_datasets()
    summary = _build_summary(datasets)
    top_indicators = _top_indicators_from_details(datasets[:8]) if datasets else []
    chronology_chart_url = reverse("dashboard:export_chronology_chart") if datasets else None

    return render(
        request,
        "dashboard.html",
        {
            "active_page": "dashboard",
            "datasets": datasets[:6],
            "summary": summary,
            "top_indicators": top_indicators,
            "chronology_chart_url": chronology_chart_url,
            "backend_error": backend_error,
        },
    )


def dataset_list(request):
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        slug = request.POST.get("slug", "").strip()

        if action == "check_export_mode":
            try:
                result = api_client.check_export_mode()
                messages.success(
                    request,
                    result.get(
                        "message",
                        "Mode API d'export actif. Aucune synchronisation distante n'est nécessaire.",
                    ),
                )
            except (BackendUnavailable, ApiError) as exc:
                messages.error(request, str(exc))

            return redirect("dashboard:dataset_list")

        if action == "delete":
            if not slug:
                messages.error(request, "Slug du dataset manquant.")
            else:
                try:
                    api_client.delete_export_dataset(slug)
                    record_audit_event(
                        request,
                        action="export_dataset_deleted",
                        object_type="export_dataset",
                        object_id=slug,
                    )
                    messages.success(request, f"Jeu de données supprimé : {slug}")
                except (BackendUnavailable, ApiError) as exc:
                    messages.error(request, str(exc))

            return redirect("dashboard:dataset_list")

    datasets, backend_error = _load_datasets()
    query = request.GET.get("q", "").strip().lower()
    country = request.GET.get("country", "").strip()
    status = request.GET.get("status", "").strip()

    countries = sorted({
        item.get("country", {}).get("name", "")
        for item in datasets
        if item.get("country")
    })

    statuses = sorted({
        item.get("last_export_status") or "inconnu"
        for item in datasets
    })

    filtered = []

    for item in datasets:
        haystack = " ".join(
            [
                item.get("title", ""),
                item.get("slug", ""),
                item.get("description", ""),
                item.get("export_id", ""),
                item.get("country", {}).get("name", ""),
            ]
        ).lower()

        if query and query not in haystack:
            continue

        if country and item.get("country", {}).get("name") != country:
            continue

        if status and (item.get("last_export_status") or "inconnu") != status:
            continue

        filtered.append(item)

    paginator = Paginator(filtered, DATASET_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "dataset_list.html",
        {
            "active_page": "datasets",
            "datasets": list(page_obj.object_list),
            "page_obj": page_obj,
            "total_count": len(datasets),
            "filtered_count": len(filtered),
            "countries": countries,
            "statuses": statuses,
            "filters": {
                "q": request.GET.get("q", ""),
                "country": country,
                "status": status,
            },
            "backend_error": backend_error,
        },
    )


def dataset_create(request):
    datasets, datasets_error = _load_datasets()
    initial = _initial_builder_state(request, datasets)
    if request.method == "GET" and not initial.get("_builder_prefill_applied"):
        _clear_builder_runtime_state(request)
    data = request.POST if request.method == "POST" else initial

    data_preview = None
    preview_columns: list[str] = []
    preview_rows: list[list[Any]] = []
    preview_error = None
    export_links = None
    links_already_generated = False
    preview_ready = False

    builder_prefill_applied = bool(initial.get("_builder_prefill_applied"))
    builder_prefill_summary = initial.get("_builder_prefill_summary")
    ai_recommendation = builder_prefill_summary
    ai_selected_indicators = initial.get("_builder_prefill_indicators", [])

    selected_source = data.get("source_code") or DEFAULT_SOURCE_CODE
    selected_topic = _clean_int(data.get("topic_id"))
    indicator_search = data.get("indicator_search", "")
    country_search = data.get("country_search", "")
    indicator_page = max(1, _clean_int(data.get("indicator_page") or request.GET.get("indicator_page")) or 1)
    source_limits, source_limits_error = _load_source_limits()
    selected_source_limit = _source_limit_for(source_limits, selected_source)

    sources, topics, indicators, countries, indicator_pagination, load_error = _load_builder_catalog(
        source_code=selected_source,
        topic_id=selected_topic,
        indicator_search=indicator_search,
        country_search=country_search,
        indicator_page=indicator_page,
    )
    backend_error = datasets_error or load_error or source_limits_error

    posted_indicator_ids = (
        request.POST.getlist("indicator_ids")
        if request.method == "POST"
        else initial.get("indicator_ids", [])
    )

    posted_indicator_ids = [str(value) for value in posted_indicator_ids]

    indicator_choices = [
        (str(item["id"]), item.get("name", str(item["id"])))
        for item in indicators
    ]

    known_choice_ids = {choice[0] for choice in indicator_choices}

    for indicator in ai_selected_indicators:
        indicator_id = str(indicator.get("id", ""))
        if indicator_id and indicator_id not in known_choice_ids:
            indicator_choices.append((indicator_id, indicator.get("name", indicator_id)))
            known_choice_ids.add(indicator_id)

    for selected_id in posted_indicator_ids:
        if str(selected_id) not in known_choice_ids:
            indicator_choices.append((str(selected_id), str(selected_id)))
            known_choice_ids.add(str(selected_id))

    form = DatasetCreateForm(
        data if request.method == "POST" else None,
        indicator_choices=indicator_choices,
        max_indicators=selected_source_limit["max_indicators_per_dataset"],
        source_label=selected_source_limit["label"],
    )

    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action", "generate_links")
        payload = _build_export_payload(form.cleaned_data)
        payload_signature = _payload_signature(payload)
        preview_ready = request.session.get("last_preview_payload") == payload_signature

        if action == "preview":
            try:
                data_preview = api_client.preview_dataset(payload, limit=50)
                record_audit_event(
                    request,
                    action="dataset_preview_launched",
                    object_type="dataset_preview",
                    extra={
                        "status": "success",
                        "source_code": payload.get("source_code"),
                        "indicator_count": len(payload.get("indicator_ids", [])),
                    },
                )
                preview_columns, preview_rows = _build_preview_table(data_preview)
                messages.success(
                    request,
                    f"Aperçu généré : {data_preview.get('preview_count', 0)} ligne(s) affichée(s) sur {data_preview.get('row_count', 0)}."
                )
                request.session["last_preview_payload"] = payload_signature
                request.session.modified = True
                preview_ready = True
            except (BackendUnavailable, ApiError) as exc:
                preview_error = str(exc)
                record_audit_event(
                    request,
                    action="dataset_preview_failed",
                    object_type="dataset_preview",
                    extra={"reason": exc.__class__.__name__},
                )
                messages.error(request, preview_error)

        elif action == "generate_links":
            if not preview_ready:
                messages.error(
                    request,
                    "Générez d'abord un aperçu des données pour cette sélection avant de créer les liens.",
                )
            else:
                cached_signature = request.session.get("last_export_payload")
                cached_links = request.session.get("last_export_links")
                if action == "generate_links" and cached_signature == payload_signature and cached_links:
                    export_links = cached_links
                    links_already_generated = True
                    messages.info(request, "Les liens ont deja ete generes pour cette selection.")
                else:
                    export_payload = dict(payload)
                    try:
                        result = api_client.generate_export_links(export_payload)
                    except (BackendUnavailable, ApiError) as exc:
                        record_audit_event(
                            request,
                            action="export_links_generation_failed",
                            object_type="export_dataset",
                            extra={"reason": exc.__class__.__name__},
                        )
                        messages.error(request, str(exc))
                    else:
                        export_links = result
                        record_audit_event(
                            request,
                            action="export_links_generated",
                            object_type="export_dataset",
                            object_id=result.get("slug", ""),
                            extra={
                                "row_count": result.get("row_count"),
                                "indicator_count": result.get("indicator_count"),
                            },
                        )
                        links_already_generated = True
                        request.session["last_export_payload"] = payload_signature
                        request.session["last_export_links"] = result
                        messages.success(
                            request,
                            f"Liens d'export generes pour : {result['slug']}",
                        )

                        if "ai_recommendation" in request.session:
                            del request.session["ai_recommendation"]
                        if "ai_user_request" in request.session:
                            del request.session["ai_user_request"]
                        if "builder_prefill" in request.session:
                            del request.session["builder_prefill"]
                        request.session.modified = True

        elif action == "regenerate_links":
            messages.error(request, "La regénération des liens n'est pas disponible depuis l'interface.")


    selected_indicator_ids_set = {str(value) for value in posted_indicator_ids}
    indicator_limit_exceeded = (
        len(selected_indicator_ids_set) > selected_source_limit["max_indicators_per_dataset"]
    )

    selected_indicators = [
        item for item in indicators
        if str(item["id"]) in selected_indicator_ids_set
    ]

    existing_selected_ids = {str(item.get("id")) for item in selected_indicators}

    for indicator in ai_selected_indicators:
        indicator_id = str(indicator.get("id", ""))
        if indicator_id in selected_indicator_ids_set and indicator_id not in existing_selected_ids:
            selected_indicators.append(
                {
                    "id": indicator.get("id"),
                    "code": indicator.get("code", ""),
                    "name": indicator.get("name", ""),
                    "unit": indicator.get("unit", "—"),
                    "periodicity": indicator.get("periodicity", "—"),
                }
            )
            existing_selected_ids.add(indicator_id)

    selected_country = _find_by_id(countries, data.get("country_id"))

    if selected_country is None and ai_recommendation:
        ai_country_id = ai_recommendation.get("country_id")
        if ai_country_id and str(ai_country_id) == str(data.get("country_id")):
            selected_country = {
                "id": ai_country_id,
                "name": ai_recommendation.get("country_name", ""),
            }

    return render(
        request,
        "dataset_create.html",
        {
            "active_page": "create",
            "form": form,
            "form_values": data,
            "datasets": datasets,
            "sources": sources,
            "topics": topics,
            "indicators": indicators,
            "indicator_pagination": indicator_pagination,
            "countries": countries,
            "selected_indicators": selected_indicators,
            "selected_indicator_ids": posted_indicator_ids,
            "selected_country": selected_country,
            "indicator_search": indicator_search,
            "country_search": country_search,
            "source_limits": source_limits,
            "source_limits_json": json.dumps(source_limits, ensure_ascii=False),
            "selected_source_limit": selected_source_limit,
            "indicator_limit_exceeded": indicator_limit_exceeded,
            "backend_error": backend_error,
            "ai_recommendation": ai_recommendation,
            "builder_prefill_applied": builder_prefill_applied,
            "ai_selected_indicators": ai_selected_indicators,
            "data_preview": data_preview,
            "preview_columns": preview_columns,
            "preview_rows": preview_rows,
            "preview_error": preview_error,
            "preview_ready": preview_ready,
            "export_links": export_links,
            "links_already_generated": links_already_generated,
            "export_is_local": _is_local_export_url(export_links.get("csv_url")) if export_links else False,
        },
    )


def dataset_detail(request, slug: str):
    opendatasoft_result = None
    opendatasoft_error = None
    if request.method == "POST" and request.POST.get("action") == "publish_to_opendatasoft":
        try:
            opendatasoft_result = api_client.publish_to_opendatasoft(slug)
            status = opendatasoft_result.get("status")
            if status == "dry_run":
                messages.info(request, "Mode dry-run : aucune publication réelle n'a été envoyée à OpenDataSoft.")
            elif status == "published":
                messages.success(request, "Dataset publié sur OpenDataSoft.")
            elif status == "updated":
                messages.success(request, "Dataset OpenDataSoft mis à jour.")
            else:
                messages.error(request, opendatasoft_result.get("error") or "Publication OpenDataSoft non aboutie.")
        except (BackendUnavailable, ApiError) as exc:
            opendatasoft_error = str(exc)
            messages.error(request, opendatasoft_error)

    try:
        detail = api_client.get_dataset_detail(slug)
    except (BackendUnavailable, ApiError) as exc:
        return render(
            request,
            "dataset_detail.html",
            {"active_page": "datasets", "backend_error": str(exc), "detail": None},
        )

    versions = detail.get("versions", [])
    version_number = _clean_int(request.GET.get("version")) or detail.get("latest_version")
    selected_version = next((item for item in versions if item.get("version") == version_number), detail.get("latest_version_detail"))
    manifest = selected_version.get("manifest", {}) if selected_version else {}
    csv_url = detail.get("csv_url") or _manifest_value(manifest, "csv_url", "url_donnees", "data_url")
    json_url = detail.get("json_url") or _manifest_value(manifest, "json_url")
    csv_view_url = _url_with_query(csv_url, preview="1") if csv_url else None
    data_url = csv_url
    opendatasoft_metadata = (
        detail.get("opendatasoft_metadata")
        or _manifest_value(manifest, "opendatasoft_metadata")
    )
    opendatasoft_status = detail.get("opendatasoft_status") or _manifest_value(manifest, "opendatasoft_status")
    opendatasoft_public_url = detail.get("opendatasoft_public_url") or _manifest_value(manifest, "opendatasoft_public_url")
    opendatasoft_last_error = detail.get("opendatasoft_last_error") or _manifest_value(manifest, "opendatasoft_last_error")
    opendatasoft_last_steps = (
        detail.get("opendatasoft_last_steps")
        or _manifest_value(manifest, "opendatasoft_last_steps")
        or []
    )
    opendatasoft_last_result = detail.get("opendatasoft_last_result") or _manifest_value(manifest, "opendatasoft_last_result")
    if not opendatasoft_metadata:
        try:
            metadata_response = api_client.get_opendatasoft_metadata(slug)
            opendatasoft_metadata = metadata_response.get("opendatasoft_metadata")
            opendatasoft_status = opendatasoft_status or metadata_response.get("opendatasoft_status")
            opendatasoft_public_url = opendatasoft_public_url or metadata_response.get("opendatasoft_public_url")
            opendatasoft_last_error = opendatasoft_last_error or metadata_response.get("opendatasoft_last_error")
            opendatasoft_last_steps = opendatasoft_last_steps or metadata_response.get("opendatasoft_last_steps") or []
            opendatasoft_last_result = opendatasoft_last_result or metadata_response.get("opendatasoft_last_result")
        except (BackendUnavailable, ApiError) as exc:
            opendatasoft_error = opendatasoft_error or str(exc)
    if opendatasoft_result:
        opendatasoft_last_error = (
            opendatasoft_result.get("opendatasoft_last_error")
            or opendatasoft_result.get("error")
            or opendatasoft_last_error
        )
        opendatasoft_last_steps = opendatasoft_result.get("opendatasoft_last_steps") or opendatasoft_last_steps

    preview = None
    preview_columns: list[str] = []
    preview_rows: list[list[Any]] = []
    preview_error = None
    preview_deferred = bool(selected_version)

    return render(
        request,
        "dataset_detail.html",
        {
            "active_page": "datasets",
            "detail": detail,
            "versions": versions,
            "selected_version": selected_version,
            "manifest": manifest,
            "data_url": data_url,
            "csv_url": csv_url,
            "csv_view_url": csv_view_url,
            "json_url": json_url,
            "export_is_local": _is_local_export_url(csv_url),
            "preview": preview,
            "preview_columns": preview_columns,
            "preview_rows": preview_rows,
            "preview_error": preview_error,
            "preview_deferred": preview_deferred,
            "manifest_json": _pretty_manifest(manifest),
            "opendatasoft_metadata": opendatasoft_metadata,
            "opendatasoft_status": opendatasoft_status,
            "opendatasoft_public_url": opendatasoft_public_url,
            "opendatasoft_last_error": opendatasoft_last_error,
            "opendatasoft_last_steps": opendatasoft_last_steps,
            "opendatasoft_last_result": opendatasoft_last_result,
            "opendatasoft_result": opendatasoft_result,
            "opendatasoft_error": opendatasoft_error,
        },
    )

def model_parameters(request):
    saved_values = request.session.get("model_parameter_values", {})
    runtime_config: dict[str, Any] = {}
    runtime_error = None
    errors: dict[str, str] = {}
    try:
        runtime_config = api_client.get_ai_runtime_config()
    except (BackendUnavailable, ApiError) as exc:
        runtime_error = str(exc)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reset":
            request.session.pop("model_parameter_values", None)
            request.session.modified = True
            try:
                runtime_config = api_client.update_ai_runtime_config(_model_parameter_payload({}))
                record_audit_event(
                    request,
                    action="ai_runtime_config_reset",
                    object_type="ai_runtime_config",
                    extra={"status": "success"},
                )
                messages.success(request, "Configuration IA réinitialisée avec les valeurs actives.")
            except (BackendUnavailable, ApiError) as exc:
                messages.warning(request, f"Configuration locale réinitialisée, mais le backend n'a pas pu être mis à jour : {exc}")
            saved_values = {}
        else:
            candidate_values, errors = _read_model_parameter_post(request)
            if errors:
                messages.error(request, "Corrigez les champs invalides avant d'enregistrer.")
                saved_values = candidate_values
            else:
                try:
                    runtime_config = api_client.update_ai_runtime_config(_model_parameter_payload(candidate_values))
                except (BackendUnavailable, ApiError) as exc:
                    record_audit_event(
                        request,
                        action="ai_runtime_config_update_failed",
                        object_type="ai_runtime_config",
                        extra={"reason": exc.__class__.__name__},
                    )
                    messages.error(request, f"Impossible d'appliquer la configuration au backend : {exc}")
                    saved_values = candidate_values
                else:
                    record_audit_event(
                        request,
                        action="ai_runtime_config_updated",
                        object_type="ai_runtime_config",
                        extra={
                            key: value
                            for key, value in candidate_values.items()
                            if "TOKEN" not in key.upper() and "KEY" not in key.upper()
                        },
                    )
                    request.session["model_parameter_values"] = candidate_values
                    request.session.modified = True
                    messages.success(request, "Configuration IA appliquée au serveur FastAPI pour la session en cours.")
                    saved_values = candidate_values

    return render(
        request,
        "model_parameters.html",
        {
            "active_page": "model_parameters",
            "groups": _build_model_parameter_groups(saved_values, errors, runtime_config=runtime_config),
            "ai_model_options_json": json.dumps(runtime_config.get("models_by_layer", {}), ensure_ascii=False),
            "has_session_overrides": bool(request.session.get("model_parameter_values")),
            "runtime_error": runtime_error,
            "runtime_config": runtime_config,
        },
    )


def mesures(request):
    mesures_data = _load_mesures(request)
    return render(
        request,
        "mesures.html",
        {
            "active_page": "mesures",
            **mesures_data,
        },
    )


def ai_evaluation(request):
    evaluation_data = _load_ai_evaluations(request)
    return render(
        request,
        "ai_evaluation.html",
        {
            "active_page": "ai_evaluation",
            **evaluation_data,
        },
    )


def performance(request):
    return redirect("dashboard:mesures")


@require_GET
def ajax_topics(request):
    try:
        data = api_client.get_topics(
            source_code=request.GET.get("source_code") or None,
            search=request.GET.get("search", ""),
        )
    except (BackendUnavailable, ApiError) as exc:
        return JsonResponse(
            {"ok": False, "message": _safe_backend_error(exc), "items": []},
            status=503,
        )
    return JsonResponse({"ok": True, "items": data})


@require_GET
def ajax_indicators(request):
    page = max(1, _clean_int(request.GET.get("page")) or 1)
    page_size = min(100, max(10, _clean_int(request.GET.get("page_size")) or INDICATOR_PAGE_SIZE))
    topic_id = _clean_int(request.GET.get("topic_id"))

    if topic_id is None:
        return JsonResponse(
            {
                "ok": True,
                "items": [],
                "message": "Veuillez sélectionner un thème avant de choisir les indicateurs.",
                "pagination": {
                    "page": 1,
                    "page_size": page_size,
                    "has_previous": False,
                    "has_next": False,
                },
            }
        )

    offset = (page - 1) * page_size

    try:
        data = api_client.get_indicators(
            source_code=request.GET.get("source_code") or None,
            topic_id=topic_id,
            search=request.GET.get("search", ""),
            limit=page_size + 1,
            offset=offset,
        )
    except (BackendUnavailable, ApiError) as exc:
        return JsonResponse(
            {"ok": False, "message": _safe_backend_error(exc), "items": []},
            status=503,
        )

    has_next = len(data) > page_size

    return JsonResponse(
        {
            "ok": True,
            "items": data[:page_size],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "has_previous": page > 1,
                "has_next": has_next,
            },
        }
    )


@require_GET
def ajax_countries(request):
    try:
        data = api_client.get_countries(search=request.GET.get("search", ""), limit=80)
    except (BackendUnavailable, ApiError) as exc:
        return JsonResponse(
            {"ok": False, "message": _safe_backend_error(exc), "items": []},
            status=503,
        )
    return JsonResponse({"ok": True, "items": data})


@require_POST
def ajax_dataset_preview(request):
    posted_indicator_ids = [str(value) for value in request.POST.getlist("indicator_ids")]
    indicator_choices = [(value, value) for value in posted_indicator_ids]
    source_limits, _ = _load_source_limits()
    selected_source_limit = _source_limit_for(source_limits, request.POST.get("source_code") or DEFAULT_SOURCE_CODE)
    form = DatasetCreateForm(
        request.POST,
        indicator_choices=indicator_choices,
        max_indicators=selected_source_limit["max_indicators_per_dataset"],
        source_label=selected_source_limit["label"],
    )

    if not form.is_valid():
        return JsonResponse(
            {
                "ok": False,
                "message": _form_error_message(form),
                "errors": form.errors.get_json_data(),
            },
            status=400,
        )

    payload = _build_export_payload(form.cleaned_data)
    try:
        data_preview = api_client.preview_dataset(payload, limit=50)
    except (BackendUnavailable, ApiError) as exc:
        record_audit_event(
            request,
            action="dataset_preview_failed",
            object_type="dataset_preview",
            extra={"reason": exc.__class__.__name__, "ajax": True},
        )
        return JsonResponse({"ok": False, "message": str(exc)}, status=503)

    preview_columns, preview_rows = _build_preview_table(data_preview)
    record_audit_event(
        request,
        action="dataset_preview_launched",
        object_type="dataset_preview",
        extra={
            "ajax": True,
            "source_code": payload.get("source_code"),
            "indicator_count": len(payload.get("indicator_ids", [])),
        },
    )
    request.session["last_preview_payload"] = _payload_signature(payload)
    request.session.modified = True

    html = render_to_string(
        "partials/dataset_preview.html",
        {
            "data_preview": data_preview,
            "preview_columns": preview_columns,
            "preview_rows": preview_rows,
            "ajax_preview": True,
        },
        request=request,
    )
    return JsonResponse(
        {
            "ok": True,
            "html": html,
            "message": (
                f"Apercu pret : {data_preview.get('preview_count', 0)} ligne(s) "
                f"affichee(s) sur {data_preview.get('row_count', 0)}."
            ),
            "row_count": data_preview.get("row_count", 0),
            "preview_count": data_preview.get("preview_count", 0),
            "non_null_value_count": data_preview.get("non_null_value_count", 0),
            "missing_indicator_count": len(data_preview.get("missing_indicator_codes", [])),
        }
    )


@require_GET
def ajax_dataset_detail_preview(request, slug: str):
    try:
        detail = api_client.get_dataset_detail(slug)
    except (BackendUnavailable, ApiError) as exc:
        html = render_to_string(
            "partials/dataset_detail_preview.html",
            {
                "preview_error": "Impossible de charger l’aperçu pour le moment.",
                "preview_error_detail": str(exc),
            },
            request=request,
        )
        return JsonResponse({"ok": False, "html": html, "message": "Impossible de charger l’aperçu."}, status=503)

    versions = detail.get("versions", [])
    version_number = _clean_int(request.GET.get("version")) or detail.get("latest_version")
    selected_version = next((item for item in versions if item.get("version") == version_number), detail.get("latest_version_detail"))
    if not selected_version:
        html = render_to_string(
            "partials/dataset_detail_preview.html",
            {"preview_empty": True},
            request=request,
        )
        return JsonResponse({"ok": True, "html": html, "message": "Aucune version disponible."})

    limit = min(max(_clean_int(request.GET.get("limit")) or 50, 1), 200)
    try:
        data_preview = api_client.get_dataset_preview(slug, selected_version["version"], limit=limit)
        preview_columns, preview_rows = _build_preview_table(data_preview)
    except (BackendUnavailable, ApiError) as exc:
        html = render_to_string(
            "partials/dataset_detail_preview.html",
            {
                "preview_error": "Impossible de charger l’aperçu pour le moment.",
                "preview_error_detail": str(exc),
            },
            request=request,
        )
        return JsonResponse({"ok": False, "html": html, "message": "Impossible de charger l’aperçu."}, status=503)

    html = render_to_string(
        "partials/dataset_detail_preview.html",
        {
            "data_preview": data_preview,
            "preview_columns": preview_columns,
            "preview_rows": preview_rows,
            "preview_limit": limit,
        },
        request=request,
    )
    return JsonResponse(
        {
            "ok": True,
            "html": html,
            "message": f"Aperçu chargé : {len(preview_rows)} ligne(s) affichée(s).",
        }
    )


def _load_datasets() -> tuple[list[dict[str, Any]], str | None]:
    try:
        return api_client.get_export_datasets(), None
    except (BackendUnavailable, ApiError) as exc:
        return [], _safe_backend_error(exc)


def _load_builder_catalog(
    *,
    source_code: str,
    topic_id: int | None,
    indicator_search: str,
    country_search: str,
    indicator_page: int,
) -> tuple[list[dict], list[dict], list[dict], list[dict], dict[str, Any], str | None]:
    indicator_offset = (max(1, indicator_page) - 1) * INDICATOR_PAGE_SIZE
    try:
        sources = api_client.get_sources()
        topics = api_client.get_topics(source_code=source_code)
        indicators = []
        if topic_id is not None:
            indicators = api_client.get_indicators(
                source_code=source_code,
                topic_id=topic_id,
                search=indicator_search,
                limit=INDICATOR_PAGE_SIZE + 1,
                offset=indicator_offset,
            )
        countries = api_client.get_countries(search=country_search, limit=80)
    except (BackendUnavailable, ApiError) as exc:
        return [], [], [], [], _indicator_pagination(indicator_page, False), str(exc)

    has_next = len(indicators) > INDICATOR_PAGE_SIZE
    return (
        sources,
        topics,
        indicators[:INDICATOR_PAGE_SIZE],
        countries,
        _indicator_pagination(indicator_page, has_next),
        None,
    )


def _indicator_pagination(page: int, has_next: bool) -> dict[str, Any]:
    page = max(1, int(page or 1))
    return {
        "page": page,
        "page_size": INDICATOR_PAGE_SIZE,
        "has_previous": page > 1,
        "has_next": has_next,
        "previous_page": max(1, page - 1),
        "next_page": page + 1,
    }


def _load_source_limits() -> tuple[dict[str, Any], str | None]:
    try:
        limits = api_client.get_source_limits()
    except (BackendUnavailable, ApiError) as exc:
        return DEFAULT_SOURCE_LIMITS, _safe_backend_error(exc)
    return limits or DEFAULT_SOURCE_LIMITS, None


def _source_limit_for(source_limits: dict[str, Any], source_code: str | None) -> dict[str, Any]:
    code = (source_code or DEFAULT_SOURCE_CODE).strip().upper()
    values = source_limits.get(code) or DEFAULT_SOURCE_LIMITS.get(code) or {
        "max_indicators_per_dataset": 60,
        "label": code or "Source",
    }
    return {
        "max_indicators_per_dataset": int(values.get("max_indicators_per_dataset") or 60),
        "label": values.get("label") or code,
    }


def _resolve_builder_prefill(prefill: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    resolved = dict(prefill)
    warnings: list[str] = []
    source_code = str(resolved.get("source_code") or DEFAULT_SOURCE_CODE).strip().upper()
    resolved["source_code"] = source_code

    topic_id = _clean_int(resolved.get("topic_id"))
    topic_name = str(resolved.get("topic_name") or "").strip()
    if topic_id is None and topic_name:
        try:
            topics = api_client.get_topics(source_code=source_code, search=topic_name)
        except (BackendUnavailable, ApiError) as exc:
            warnings.append(f"Le thème proposé n'a pas été vérifié : {exc}")
            topics = []
        topic = _find_by_normalized_name(topics, topic_name)
        if topic:
            topic_id = _clean_int(topic.get("id"))
            resolved["topic_name"] = topic.get("name") or topic_name
        else:
            warnings.append("Le thème proposé n'a pas été retrouvé. Sélectionnez un thème manuellement.")
    resolved["topic_id"] = topic_id or ""

    country_id = _clean_int(resolved.get("country_id"))
    country_name = str(resolved.get("country_name") or "").strip()
    if country_id is None and country_name:
        try:
            countries = api_client.get_countries(search=country_name, limit=80)
        except (BackendUnavailable, ApiError) as exc:
            warnings.append(f"Le pays proposé n'a pas été vérifié : {exc}")
            countries = []
        country = _find_by_normalized_name(countries, country_name)
        if country:
            country_id = _clean_int(country.get("id"))
            resolved["country_name"] = country.get("name") or country_name
            resolved["country_code"] = country.get("code_iso3") or country.get("wb_code") or resolved.get("country_code", "")
        else:
            warnings.append("Le pays proposé est introuvable. Sélectionnez un pays manuellement.")
    resolved["country_id"] = country_id or ""

    indicator_ids = [
        int(value)
        for value in resolved.get("indicator_ids", [])
        if _clean_int(value) is not None
    ]
    indicator_codes = [
        str(value).strip()
        for value in resolved.get("indicator_codes", [])
        if str(value).strip()
    ]
    indicator_details = [
        {
            "id": item.get("id"),
            "code": item.get("code", ""),
            "name": item.get("name", ""),
        }
        for item in resolved.get("indicator_details", [])
        if _clean_int(item.get("id")) is not None
    ]
    if not indicator_details and indicator_ids:
        indicator_details = [
            {
                "id": indicator_id,
                "code": code if index < len(indicator_codes) else "",
                "name": code if index < len(indicator_codes) else str(indicator_id),
            }
            for index, indicator_id in enumerate(indicator_ids)
        ]
    if not indicator_ids and indicator_codes:
        indicator_details = _resolve_indicator_details(
            source_code=source_code,
            topic_id=topic_id,
            indicator_codes=indicator_codes,
        )
        resolved_codes = {item.get("code") for item in indicator_details}
        missing_codes = [code for code in indicator_codes if code not in resolved_codes]
        if missing_codes:
            warnings.append(
                "Certains indicateurs proposés sont introuvables et ont été ignorés : "
                + ", ".join(missing_codes)
            )
        indicator_ids = [int(item["id"]) for item in indicator_details if _clean_int(item.get("id")) is not None]

    if not indicator_ids:
        warnings.append("Aucun indicateur valide dans la proposition. Sélectionnez les indicateurs manuellement.")

    resolved["indicator_ids"] = indicator_ids
    resolved["indicator_codes"] = [item.get("code", "") for item in indicator_details] or indicator_codes
    resolved["indicator_details"] = indicator_details
    return resolved, warnings


def _resolve_indicator_details(
    *,
    source_code: str,
    topic_id: int | None,
    indicator_codes: list[str],
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for code in indicator_codes:
        candidates: list[dict[str, Any]] = []
        for lookup_topic_id in (topic_id, None):
            try:
                candidates = api_client.get_indicators(
                    source_code=source_code,
                    topic_id=lookup_topic_id,
                    search=code,
                    limit=10,
                    offset=0,
                )
            except (BackendUnavailable, ApiError):
                candidates = []
            match = next(
                (item for item in candidates if str(item.get("code", "")).strip().upper() == code.upper()),
                None,
            )
            if match:
                indicator_id = str(match.get("id", ""))
                if indicator_id and indicator_id not in seen_ids:
                    details.append(
                        {
                            "id": match.get("id"),
                            "code": match.get("code", code),
                            "name": match.get("name", ""),
                        }
                    )
                    seen_ids.add(indicator_id)
                break
    return details


def _find_by_normalized_name(items: list[dict[str, Any]], wanted_name: str) -> dict[str, Any] | None:
    wanted = _normalize_label(wanted_name)
    if not wanted:
        return None
    for item in items:
        candidate = _normalize_label(item.get("name") or item.get("label") or "")
        if candidate == wanted:
            return item
    return next(
        (
            item
            for item in items
            if wanted in _normalize_label(item.get("name") or item.get("label") or "")
            or _normalize_label(item.get("name") or item.get("label") or "") in wanted
        ),
        None,
    )


def _normalize_label(value: str | None) -> str:
    text = (
        (value or "")
        .replace("\u0153", "oe")
        .replace("\u0152", "OE")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
    )
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


def _initial_builder_state(request, datasets: list[dict[str, Any]]) -> dict[str, Any]:
    mode = request.GET.get("mode", "new")
    existing_slug = request.GET.get("existing_slug", "")

    initial: dict[str, Any] = {
        "mode": mode if mode in {"new", "version"} else "new",
        "existing_slug": existing_slug,
        "source_code": DEFAULT_SOURCE_CODE,
        "topic_id": "",
        "indicator_ids": [],
        "indicator_page": str(_clean_int(request.GET.get("indicator_page")) or 1),
        "country_id": "",
        "country_search": "",
        "indicator_search": "",
        "start_date": DEFAULT_START_DATE,
        "end_date": DEFAULT_END_DATE,
        "title": "",
        "description": "",
    }

    builder_prefill = request.session.pop("builder_prefill", None)
    if builder_prefill:
        request.session.pop("ai_recommendation", None)
        request.session.modified = True

    if builder_prefill and not existing_slug:
        resolved_prefill, warnings = _resolve_builder_prefill(builder_prefill)
        for warning in warnings:
            messages.warning(request, warning)

        initial.update(
            {
                "mode": "new",
                "existing_slug": "",
                "source_code": resolved_prefill.get("source_code", DEFAULT_SOURCE_CODE),
                "topic_id": str(resolved_prefill.get("topic_id") or ""),
                "indicator_ids": [
                    str(value)
                    for value in resolved_prefill.get("indicator_ids", [])
                    if _clean_int(value) is not None
                ],
                "country_id": str(resolved_prefill.get("country_id") or ""),
                "country_search": resolved_prefill.get("country_name", ""),
                "indicator_search": "",
                "start_date": resolved_prefill.get("start_date", DEFAULT_START_DATE),
                "end_date": resolved_prefill.get("end_date", DEFAULT_END_DATE),
                "title": resolved_prefill.get("title", ""),
                "description": resolved_prefill.get("description", ""),
                "_builder_prefill_applied": True,
                "_builder_prefill_summary": {
                    "confidence": "préremplissage",
                    "title": resolved_prefill.get("title", ""),
                    "source_code": resolved_prefill.get("source_code", DEFAULT_SOURCE_CODE),
                    "country_name": resolved_prefill.get("country_name", ""),
                    "country_code": resolved_prefill.get("country_code", ""),
                    "topic_name": resolved_prefill.get("topic_name", ""),
                },
                "_builder_prefill_indicators": resolved_prefill.get("indicator_details", []),
            }
        )
        return initial

    if existing_slug and any(item["slug"] == existing_slug for item in datasets):
        try:
            detail = api_client.get_dataset_detail(existing_slug)
        except (BackendUnavailable, ApiError):
            return initial

        latest = detail.get("latest_version_detail", {})
        manifest = latest.get("manifest", {})

        source_codes = _manifest_value(
            manifest,
            "codes_sources",
            "source_codes",
            default=[DEFAULT_SOURCE_CODE],
        )

        topic_ids = _manifest_value(
            manifest,
            "ids_themes",
            "topic_ids",
            default=[],
        )

        initial.update(
            {
                "mode": "version",
                "source_code": source_codes[0] if source_codes else DEFAULT_SOURCE_CODE,
                "topic_id": str(topic_ids[0]) if topic_ids else "",
                "indicator_ids": [
                    str(item["id"])
                    for item in latest.get("indicators", [])
                    if item.get("id")
                ],
                "country_id": str(latest.get("country", {}).get("id", "")),
                "country_search": latest.get("country", {}).get("name", ""),
                "indicator_search": "",
                "start_date": latest.get("start_date", DEFAULT_START_DATE),
                "end_date": latest.get("end_date", DEFAULT_END_DATE),
                "title": detail.get("title", ""),
                "description": detail.get("description", ""),
            }
        )

    return initial


def _clear_builder_runtime_state(request) -> None:
    removed = False
    for key in ("last_preview_payload", "last_export_payload", "last_export_links"):
        if key in request.session:
            request.session.pop(key, None)
            removed = True
    if removed:
        request.session.modified = True


def _build_export_payload(cleaned: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_code": cleaned["source_code"],
        "topic_id": cleaned.get("topic_id"),
        "indicator_ids": [int(value) for value in cleaned["indicator_ids"]],
        "country_id": cleaned["country_id"],
        "start_date": cleaned["start_date"].isoformat(),
        "end_date": cleaned["end_date"].isoformat(),
        "title": cleaned["title"],
        "description": cleaned["description"],
        "existing_slug": cleaned["existing_slug"] if cleaned["mode"] == "version" else None,
        "format": "csv",
    }


def _build_summary(datasets: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "datasets": len(datasets),
        "versions": sum(int(item.get("latest_version") or 0) for item in datasets),
        "countries": len({item.get("country", {}).get("name") for item in datasets if item.get("country")}),
        "indicators": _count_indicators_from_details(datasets[:8]),
    }


def _count_indicators_from_details(datasets: list[dict[str, Any]]) -> int:
    indicator_ids: set[int] = set()
    for item in datasets:
        try:
            detail = api_client.get_dataset_detail(item["slug"])
        except (BackendUnavailable, ApiError):
            continue
        for version in detail.get("versions", []):
            for indicator in version.get("indicators", []):
                indicator_ids.add(indicator["id"])
    return len(indicator_ids)


def _top_indicators_from_details(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str]] = Counter()
    for item in datasets:
        try:
            detail = api_client.get_dataset_detail(item["slug"])
        except (BackendUnavailable, ApiError):
            continue
        for version in detail.get("versions", []):
            for indicator in version.get("indicators", []):
                counter[(indicator.get("code", ""), indicator.get("name", ""))] += 1
    return [
        {"code": code, "name": name, "count": count}
        for (code, name), count in counter.most_common(6)
    ]


def _manifest_value(manifest: dict[str, Any], *keys: str, default=None):
    for key in keys:
        if key in manifest:
            return manifest[key]
    return default


def _pretty_manifest(manifest: dict[str, Any]) -> str:
    import json

    return json.dumps(manifest or {}, ensure_ascii=False, indent=2)


def _payload_signature(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _is_local_export_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "127.0.0.1" in lowered or "localhost" in lowered


def _url_with_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _build_model_parameter_groups(
    saved_values: dict[str, Any],
    errors: dict[str, str],
    *,
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    runtime_config = runtime_config or {}
    provider_options = runtime_config.get("providers_by_layer") or {}
    disabled_options = runtime_config.get("disabled_providers_by_layer") or {}
    model_options = runtime_config.get("models_by_layer") or {}
    resolved_values: dict[str, Any] = {}
    for group in MODEL_PARAMETER_GROUPS:
        fields = []
        for field in group["fields"]:
            name = field["name"]
            value = saved_values.get(name, runtime_config.get(name, _current_env_value(name)))
            options = field.get("options")
            layer = field.get("layer")
            if layer and name.endswith("_PROVIDER"):
                options = [
                    {"value": item["code"], "label": item["label"], "disabled": False, "reason": ""}
                    for item in provider_options.get(layer, [])
                ]
                if options and value not in {item["value"] for item in options}:
                    value = options[0]["value"]
            elif layer and name.endswith("_MODEL"):
                provider_field = field.get("provider_field", "")
                provider_name = resolved_values.get(
                    provider_field,
                    saved_values.get(provider_field, runtime_config.get(provider_field, "")),
                )
                provider_models = model_options.get(layer, {}).get(provider_name, [])
                options = [
                    {"value": item, "label": item, "disabled": False, "reason": ""}
                    for item in provider_models
                ]
                if options and value not in {item["value"] for item in options}:
                    value = options[0]["value"]
            elif field.get("options_env"):
                options = [
                    {"value": option.strip(), "label": option.strip(), "disabled": False, "reason": ""}
                    for option in os.getenv(field["options_env"], "").split(",")
                    if option.strip()
                ]
            elif options:
                options = [
                    {"value": option, "label": _model_option_label(option), "disabled": False, "reason": ""}
                    for option in options
                ]
            fields.append(
                {
                    **field,
                    "value": value,
                    "checked": _normalise_runtime_bool(value) if field["type"] == "boolean" else False,
                    "options": options or [],
                    "error": errors.get(name),
                    "effective_env": name,
                }
            )
            resolved_values[name] = value
        groups.append({**group, "fields": fields})
        layer = next((field.get("layer") for field in group["fields"] if field.get("layer")), None)
        if layer:
            groups[-1]["disabled_providers"] = disabled_options.get(layer, [])
    return groups


def _model_option_label(option: str) -> str:
    labels = {
        "gemini": "Gemini",
        "audit_only": "Audit uniquement",
        "always": "Toujours",
        "off": "Désactivé",
    }
    return labels.get(option, option)


def _model_parameter_payload(values: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for group in MODEL_PARAMETER_GROUPS:
        for field in group["fields"]:
            name = field["name"]
            raw_value = values.get(name, _current_env_value(name))
            if field["type"] == "boolean":
                payload[name] = _normalise_runtime_bool(raw_value)
            elif field["type"] == "range":
                number = float(raw_value or field["min"])
                payload[name] = number if field.get("value_type") == "float" else int(number)
            else:
                payload[name] = str(raw_value).strip()
    return payload


def _normalise_runtime_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_model_parameter_post(request) -> tuple[dict[str, Any], dict[str, str]]:
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for group in MODEL_PARAMETER_GROUPS:
        for field in group["fields"]:
            name = field["name"]
            raw_value = request.POST.get(name, "")
            if field["type"] == "boolean":
                values[name] = "1" if request.POST.get(name) == "on" else "0"
                continue
            if field["type"] == "range":
                try:
                    number = float(raw_value)
                except ValueError:
                    errors[name] = "Valeur numerique invalide."
                    values[name] = raw_value
                    continue
                if number < field["min"] or number > field["max"]:
                    errors[name] = f"Valeur attendue entre {field['min']} et {field['max']}."
                values[name] = str(int(number) if float(number).is_integer() else number)
                continue
            if field["type"] == "select":
                options = field.get("options") or [
                    option.strip()
                    for option in os.getenv(field.get("options_env", ""), "").split(",")
                    if option.strip()
                ]
                if options and raw_value not in options:
                    errors[name] = "Option invalide."
                values[name] = raw_value.strip()
                continue
            values[name] = raw_value.strip()
            if name.endswith("_MODEL") and not values[name]:
                errors[name] = "Le modèle est obligatoire."
    return values, errors


def _current_env_value(name: str) -> str:
    fallback_values = {
        "AI_PROVIDER": "gemini",
        "AI_MODEL": os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        "AI_TEMPERATURE": "0",
        "AI_ENABLE_BUSINESS_RULES": "1",
        "AI_MAX_CANDIDATES": "40",
        "AI_TARGET_INDICATORS": "5",
        "WB_MAX_INDICATORS_PER_DATASET": "60",
    }
    return str(os.getenv(name, fallback_values.get(name, "")))


def _load_mesures(request=None) -> dict[str, Any]:
    mesures_base = _decorate_mesures(_read_mesure_jsonl("mesures_db.jsonl", limit=60))
    mesures_datasets = _decorate_mesures(_read_mesure_jsonl("mesures_datasets.jsonl", limit=50))
    mesures_ia = _decorate_mesures(_read_mesure_jsonl("mesures_ia.jsonl", limit=30))
    mesures_premier_lancement = [
        row
        for row in mesures_base
        if row.get("type") == "mesure_premier_lancement_base_temporaire"
    ]
    mesures_sync_reelles = [
        row
        for row in mesures_base
        if row.get("type") == "synchronisation_banque_mondiale" and _has_http_calls(row)
    ]
    mesures_sync_ignorees = [
        row
        for row in mesures_base
        if row.get("type") == "synchronisation_banque_mondiale" and not _has_http_calls(row)
    ]
    mesures_apercus = [row for row in mesures_datasets if row.get("type") == "apercu_dataset"]
    mesures_generation_liens = [row for row in mesures_datasets if row.get("type") == "generation_liens_dataset"]
    mesures_ia_normal = [row for row in mesures_ia if row.get("type") == "assistant_ia_normal"]
    mesures_ia_audit = [row for row in mesures_ia if row.get("type") == "assistant_ia_audit"]
    mesures_ia_local = [row for row in mesures_ia if row.get("type") == "assistant_ia_local"]
    grandes_requetes_all = sorted(
        mesures_datasets,
        key=lambda row: int(row.get("nombre_indicateurs") or 0),
        reverse=True,
    )
    page_premier_lancement = _paginate_list(request, mesures_premier_lancement, "page_base")
    page_sync_reelles = _paginate_list(request, mesures_sync_reelles, "page_sync")
    page_sync_ignorees = _paginate_list(request, mesures_sync_ignorees, "page_courtes")
    page_datasets = _paginate_list(request, mesures_datasets, "page_jeux")
    page_ia = _paginate_list(request, mesures_ia, "page_ia")
    page_grandes_requetes = _paginate_list(request, grandes_requetes_all, "page_grandes", reverse=False)
    return {
        "mesures_premier_lancement": list(page_premier_lancement.object_list),
        "mesures_sync_reelles": list(page_sync_reelles.object_list),
        "mesures_sync_ignorees": list(page_sync_ignorees.object_list),
        "mesures_datasets": list(page_datasets.object_list),
        "mesures_ia": list(page_ia.object_list),
        "page_premier_lancement": page_premier_lancement,
        "page_sync_reelles": page_sync_reelles,
        "page_sync_ignorees": page_sync_ignorees,
        "page_mesures_datasets": page_datasets,
        "page_mesures_ia": page_ia,
        "grandes_requetes": list(page_grandes_requetes.object_list),
        "page_grandes_requetes": page_grandes_requetes,
        "resume_premier_lancement": _resume_mesures(mesures_premier_lancement),
        "resume_sync_reelles": _resume_mesures(mesures_sync_reelles),
        "resume_sync_ignorees": _resume_mesures(mesures_sync_ignorees),
        "resume_datasets": _resume_mesures(mesures_datasets),
        "resume_apercus": _resume_mesures(mesures_apercus),
        "resume_generation_liens": _resume_mesures(mesures_generation_liens),
        "resume_ia": _resume_mesures(mesures_ia),
        "resume_ia_normal": _resume_mesures(mesures_ia_normal),
        "resume_ia_audit": _resume_mesures(mesures_ia_audit),
        "resume_ia_local": _resume_mesures(mesures_ia_local),
        "dossier_mesures": "Données de pilotage",
    }


def _load_ai_evaluations(request=None) -> dict[str, Any]:
    raw_rows = _read_mesure_jsonl("ia_decisions.jsonl", limit=80)
    legacy_rows = _read_mesure_jsonl("evaluations_ia.jsonl", limit=80)
    combined_rows = sorted(
        [*legacy_rows, *raw_rows],
        key=lambda row: str(row.get("date", "")),
    )
    evaluations = _decorate_ai_evaluations(
        combined_rows[-80:]
    )
    page_evaluations = _paginate_list(request, evaluations, "page")
    return {
        "evaluations": list(page_evaluations.object_list),
        "page_evaluations": page_evaluations,
        "resume_evaluations": _resume_mesures(evaluations),
        "dossier_mesures": "Données de pilotage",
    }


def _paginate_list(request, rows: list[dict[str, Any]], page_param: str, *, reverse: bool = True):
    ordered_rows = list(reversed(rows)) if reverse else list(rows)
    paginator = Paginator(ordered_rows, MEASURE_PAGE_SIZE)
    page_number = request.GET.get(page_param) if request is not None else 1
    return paginator.get_page(page_number)


def _read_mesure_jsonl(filename: str, *, limit: int) -> list[dict[str, Any]]:
    path = _measure_dir() / filename
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _decorate_mesures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = {
        "synchronisation_banque_mondiale": "Synchronisation Banque mondiale",
        "mesure_premier_lancement_base_temporaire": "Mesure premier lancement",
        "apercu_dataset": "Aperçu jeu de données",
        "generation_liens_dataset": "Génération des liens Opendatasoft",
        "assistant_ia": "Assistant IA",
        "assistant_ia_normal": "Assistant IA normal",
        "assistant_ia_audit": "Assistant IA audit",
        "assistant_ia_local": "Assistant IA local",
        "test_assistant_ia": "Test Assistant IA",
    }
    decorated = []
    for row in rows:
        item = dict(row)
        for field in NUMERIC_MEASURE_FIELDS:
            if field not in item or item.get(field) == "":
                item[field] = None
        item["type_affiche"] = labels.get(str(row.get("type", "")), str(row.get("type", "-")).replace("_", " "))
        decorated.append(item)
    return decorated


def _decorate_ai_evaluations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decorated = []
    for row in rows:
        item = dict(row)
        if "analyse_normalisee" not in item:
            item["analyse_normalisee"] = {
                "source": item.get("analyse_ia", {}).get("source"),
                "pays": item.get("analyse_ia", {}).get("pays"),
                "annee_debut": item.get("analyse_ia", {}).get("annee_debut"),
                "annee_fin": item.get("analyse_ia", {}).get("annee_fin"),
                "mots_cles": item.get("analyse_ia", {}).get("mots_cles", []),
                "confiance_declaree_ia": item.get("analyse_ia", {}).get("confiance"),
            }
        item.setdefault("analyse_ia", {})
        item.setdefault("recherche_locale", {})
        item.setdefault("selection_finale", {})
        item.setdefault("validation", {})
        item.setdefault("evaluation_ia", {})
        item["recherche_locale"].setdefault(
            "nombre_candidats",
            item["recherche_locale"].get("nombre_candidats_avant_limite", 0),
        )
        item["recherche_locale"].setdefault("nombre_candidats_apres_regles", "-")
        item["recherche_locale"].setdefault("nombre_candidats_envoyes", 0)
        item["recherche_locale"].setdefault("nombre_bloques", 0)
        item["recherche_locale"].setdefault("limite_candidats_ia", "-")
        item.setdefault("points_forts", [])
        item.setdefault("points_faibles", [])
        item.setdefault("regles_metier", [])
        item.setdefault("etapes", {})
        item["source_execution"] = item.get("source_execution") or "unknown"
        item["triggered_by"] = item.get("triggered_by") or "unknown"
        item["source_execution_affichee"] = _execution_source_label(item["source_execution"])
        item["triggered_by_affiche"] = _trigger_label(item["triggered_by"])
        item["pipeline_version"] = item.get("pipeline_version") or "historique"
        item["run_id"] = item.get("run_id") or "-"
        item["etat_technique"] = item.get("etat_technique") or item.get("etat") or "-"
        item["etat_metier"] = item.get("etat_metier") or "-"
        evaluation_result = item.get("evaluation_ia", {}).get("result", {})
        item["decision_evaluateur"] = item.get("decision_evaluateur") or evaluation_result.get("judge_decision") or "-"
        item["avis_evaluateur"] = evaluation_result
        item["avis_evaluateur"]["explanation_affichee"] = _french_evaluator_explanation(evaluation_result)
        item["donnees_verifiees"] = bool(
            item.get("validation", {}).get("donnees_verifiees")
            or item.get("validation", {}).get("data_preview_available")
            or item.get("evaluation_ia", {}).get("evidence", {}).get("backend_validation", {}).get("data_preview_available")
        )
        item["acceptes"] = item["selection_finale"].get("acceptes", [])
        item["non_retenus"] = item["selection_finale"].get("non_retenus", item["selection_finale"].get("rejetes", []))
        item["bloques"] = item["selection_finale"].get("bloques", [])
        item["invalides"] = item["selection_finale"].get("invalides", [])
        item["mots_cles"] = item["analyse_normalisee"].get("mots_cles", item["analyse_ia"].get("mots_cles", []))
        item["candidats_principaux"] = item["recherche_locale"].get("candidats_principaux", [])
        item["indicateurs_selectionnes_count"] = len(item["acceptes"])
        item["limite_source"] = item.get("validation", {}).get("limite_source") or _source_limit_for(DEFAULT_SOURCE_LIMITS, "WB")["max_indicators_per_dataset"]
        item["source_limite_label"] = "Banque mondiale"
        raw_error = _first_non_empty(
            item.get("erreur"),
            item.get("error"),
            item.get("detail"),
            item.get("message"),
            item.get("evaluation_ia", {}).get("error"),
        )
        if not raw_error:
            raw_error = _first_technical_weakness(item.get("points_faibles", []))
        item["technical_error_display"] = raw_error or ""
        item["raw_error_display"] = _clean_ai_error_message(raw_error) if raw_error else ""
        item["risk_level"] = item["etat_metier"] or item["decision_evaluateur"] or "-"
        decorated.append(item)
    return decorated


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _first_technical_weakness(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    for value in values:
        text = str(value).strip()
        if _looks_technical_error(text):
            return text
    return ""


def _looks_technical_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("traceback", "exception", "resource_exhausted", "unavailable", "overloaded", "503", "429", "quota", "json")
    )


def _clean_ai_error_message(error: str) -> str:
    lowered = str(error).lower()
    if any(marker in lowered for marker in ("503", "unavailable", "overloaded", "high demand", "surcharge")):
        return "Le service IA externe est temporairement indisponible. Réessayez plus tard ou utilisez les règles locales."
    if any(marker in lowered for marker in ("429", "resource_exhausted", "quota")):
        return "Quota IA atteint. Réessayez plus tard ou utilisez les règles locales."
    return str(error)


def _execution_source_label(value: str) -> str:
    labels = {
        "assistant_ui": "Assistant interface",
        "tests_ai": "Tests IA",
        "manual_command": "Commande manuelle",
        "system_debug": "Diagnostic système",
        "unknown": "Origine inconnue",
    }
    return labels.get(str(value), str(value).replace("_", " "))


def _trigger_label(value: str) -> str:
    labels = {
        "user_click": "Clic utilisateur",
        "command_line": "Ligne de commande",
        "page_load": "Chargement de page",
        "test_suite": "Suite de tests",
        "unknown": "Inconnu",
    }
    return labels.get(str(value), str(value).replace("_", " "))


def _french_evaluator_explanation(result: dict[str, Any]) -> str:
    explanation = str(result.get("explanation") or "").strip()
    if explanation and not _looks_english(explanation):
        return explanation
    decision = str(result.get("judge_decision") or "unknown")
    labels = {
        "acceptable": "L'évaluateur juge la recommandation acceptable selon les preuves disponibles.",
        "weak": "L'évaluateur signale une recommandation plausible mais fragile ou incomplète.",
        "incoherent": "L'évaluateur signale une incohérence entre la demande et les indicateurs retenus.",
        "unknown": "L'évaluateur n'a pas assez de preuves pour conclure.",
        "-": "Aucun avis IA disponible pour cette exécution.",
    }
    return labels.get(decision, "Aucun avis IA disponible pour cette exécution.")


def _looks_english(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("the user", "selected indicators", "data preview", "backend", "request", "however")
    )


def _has_http_calls(row: dict[str, Any]) -> bool:
    try:
        return int(row.get("nombre_appels_http") or 0) > 0
    except (TypeError, ValueError):
        return False


def _measure_dir() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return Path(os.getenv("DATABRIDGE_MEASURE_DIR", str(project_root / "logs" / "mesures")))


def _resume_mesures(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        float(row.get("duree_totale_secondes"))
        for row in rows
        if row.get("duree_totale_secondes") not in (None, "")
    ]
    if not values:
        return {"nombre": 0, "moyenne": "-", "minimum": "-", "maximum": "-", "qualite": ""}
    minimum = min(values)
    maximum = max(values)
    quality_flags: list[str] = []
    if len(values) < 3:
        quality_flags.append("Échantillon faible — moyenne non fiable")
    if len(values) >= 2 and minimum > 0 and maximum / minimum >= 3:
        quality_flags.append("Variation forte")
    return {
        "nombre": len(values),
        "moyenne": f"{sum(values) / len(values):.2f} s",
        "minimum": f"{minimum:.2f} s",
        "maximum": f"{maximum:.2f} s",
        "qualite": " · ".join(quality_flags),
    }


def _find_by_id(items: list[dict[str, Any]], value) -> dict[str, Any] | None:
    wanted = _clean_int(value)
    if wanted is None:
        return None
    return next((item for item in items if item.get("id") == wanted), None)


def _clean_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
