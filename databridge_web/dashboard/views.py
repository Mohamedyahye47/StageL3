from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any


from dashboard.services.api_client import get_ai_dataset_recommendation
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from .forms import DatasetCreateForm
from .services import api_client
from .services.api_client import ApiError, BackendUnavailable


DEFAULT_SOURCE_CODE = "WB"
DEFAULT_START_DATE = date(2020, 1, 1).isoformat()
DEFAULT_END_DATE = date(2024, 12, 31).isoformat()


def ai_assistant(request):
    recommendation = None
    error = None
    user_request = ""

    if request.method == "POST":
        action = request.POST.get("action", "generate")

        if action == "generate":
            user_request = request.POST.get("user_request", "").strip()

            if not user_request:
                error = "Veuillez décrire votre besoin d'analyse."
            else:
                try:
                    recommendation = get_ai_dataset_recommendation(user_request)
                    request.session["ai_recommendation"] = recommendation
                    request.session["ai_user_request"] = user_request
                    request.session.modified = True
                except Exception as exc:
                    error = f"Assistant IA indisponible: {exc}"

        elif action == "apply":
            recommendation = request.session.get("ai_recommendation")

            if not recommendation:
                error = "Aucune proposition IA disponible. Générez d'abord une proposition."
            else:
                return redirect("dashboard:dataset_create")

    else:
        user_request = request.session.get("ai_user_request", "")
        recommendation = request.session.get("ai_recommendation")

    return render(
        request,
        "ai_assistant.html",
        {
            "active_page": "assistant",
            "user_request": user_request,
            "recommendation": recommendation,
            "error": error,
        },
    )



def dashboard(request):
    datasets, backend_error = _load_datasets()
    summary = _build_summary(datasets)
    top_indicators = _top_indicators_from_details(datasets[:8]) if datasets else []
    timeline = _build_timeline(datasets)

    return render(
        request,
        "dashboard.html",
        {
            "active_page": "dashboard",
            "datasets": datasets[:6],
            "summary": summary,
            "top_indicators": top_indicators,
            "timeline": timeline,
            "backend_error": backend_error,
        },
    )


def dataset_list(request):
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        slug = request.POST.get("slug", "").strip()

        if action == "sync_hf":
            try:
                result = api_client.sync_published_datasets_with_hf()
                messages.success(
                    request,
                    f"Synchronisation terminée : {result.get('removed_count', 0)} dataset(s) supprimé(s) du miroir local."
                )
            except (BackendUnavailable, ApiError) as exc:
                messages.error(request, str(exc))

            return redirect("dashboard:dataset_list")

        if action == "delete":
            if not slug:
                messages.error(request, "Slug du dataset manquant.")
            else:
                try:
                    api_client.delete_published_dataset(slug)
                    messages.success(request, f"Dataset supprimé : {slug}")
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
        item.get("last_publish_status") or "inconnu"
        for item in datasets
    })

    filtered = []

    for item in datasets:
        haystack = " ".join(
            [
                item.get("title", ""),
                item.get("slug", ""),
                item.get("description", ""),
                item.get("remote_id", ""),
                item.get("country", {}).get("name", ""),
            ]
        ).lower()

        if query and query not in haystack:
            continue

        if country and item.get("country", {}).get("name") != country:
            continue

        if status and (item.get("last_publish_status") or "inconnu") != status:
            continue

        filtered.append(item)

    return render(
        request,
        "dataset_list.html",
        {
            "active_page": "datasets",
            "datasets": filtered,
            "total_count": len(datasets),
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
    data = request.POST if request.method == "POST" else initial

    data_preview = None
    preview_columns: list[str] = []
    preview_rows: list[list[Any]] = []
    preview_error = None

    ai_recommendation = request.session.get("ai_recommendation")
    ai_selected_indicators = []
    if ai_recommendation:
        ai_selected_indicators = ai_recommendation.get("indicators", [])

    selected_source = data.get("source_code") or DEFAULT_SOURCE_CODE
    selected_topic = _clean_int(data.get("topic_id"))
    indicator_search = data.get("indicator_search", "")
    country_search = data.get("country_search", "")

    sources, topics, indicators, countries, load_error = _load_builder_catalog(
        source_code=selected_source,
        topic_id=selected_topic,
        indicator_search=indicator_search,
        country_search=country_search,
    )
    backend_error = datasets_error or load_error

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
    )

    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action", "publish")
        payload = _build_publish_payload(form.cleaned_data)

        if action == "preview":
            try:
                data_preview = api_client.preview_dataset(payload, limit=50)
                preview_columns = data_preview.get("columns", [])
                preview_rows = [
                    [row.get(column, "") for column in preview_columns]
                    for row in data_preview.get("rows", [])
                ]
                messages.success(
                    request,
                    f"Aperçu généré : {data_preview.get('preview_count', 0)} ligne(s) affichée(s) sur {data_preview.get('row_count', 0)}."
                )
            except (BackendUnavailable, ApiError) as exc:
                preview_error = str(exc)
                messages.error(request, preview_error)

        elif action == "publish":
            try:
                result = api_client.publish_dataset(payload)
            except (BackendUnavailable, ApiError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"Dataset publié avec succès : {result['slug']} {result['remote_version']}",
                )

                if "ai_recommendation" in request.session:
                    del request.session["ai_recommendation"]
                if "ai_user_request" in request.session:
                    del request.session["ai_user_request"]
                request.session.modified = True

                return redirect("dashboard:dataset_detail", slug=result["slug"])

    selected_indicator_ids_set = {str(value) for value in posted_indicator_ids}

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
            "countries": countries,
            "selected_indicators": selected_indicators,
            "selected_indicator_ids": posted_indicator_ids,
            "selected_country": selected_country,
            "indicator_search": indicator_search,
            "country_search": country_search,
            "backend_error": backend_error,
            "ai_recommendation": ai_recommendation,
            "ai_selected_indicators": ai_selected_indicators,
            "data_preview": data_preview,
            "preview_columns": preview_columns,
            "preview_rows": preview_rows,
            "preview_error": preview_error,
        },
    )


def dataset_detail(request, slug: str):
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
    data_url = _manifest_value(manifest, "url_donnees", "data_url")

    preview = None
    preview_columns: list[str] = []
    preview_rows: list[list[Any]] = []
    preview_error = None
    if selected_version:
        try:
            preview = api_client.get_dataset_preview(slug, selected_version["version"], limit=30)
            preview_columns = preview.get("columns", [])
            preview_rows = [
                [row.get(column, "") for column in preview_columns]
                for row in preview.get("rows", [])
            ]
        except (BackendUnavailable, ApiError) as exc:
            preview_error = str(exc)

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
            "preview": preview,
            "preview_columns": preview_columns,
            "preview_rows": preview_rows,
            "preview_error": preview_error,
            "manifest_json": _pretty_manifest(manifest),
        },
    )


def health(request):
    statuses = []
    try:
        datasets = api_client.get_published_datasets()
        statuses.append({"label": "Backend FastAPI", "ok": True, "message": "API accessible."})
        statuses.append({"label": "Base locale", "ok": True, "message": f"{len(datasets)} dataset(s) reflété(s)."})
    except BackendUnavailable as exc:
        statuses.append({"label": "Backend FastAPI", "ok": False, "message": str(exc)})
        statuses.append({"label": "Base locale", "ok": False, "message": "Impossible de vérifier la base via l'API."})
    except ApiError as exc:
        statuses.append({"label": "Backend FastAPI", "ok": False, "message": str(exc)})

    try:
        hf = api_client.get_hf_health()
        statuses.append({"label": "Hugging Face", "ok": bool(hf.get("ok")), "message": hf.get("message", "")})
    except (BackendUnavailable, ApiError) as exc:
        statuses.append({"label": "Hugging Face", "ok": False, "message": str(exc)})

    return render(
        request,
        "health.html",
        {"active_page": "health", "statuses": statuses},
    )


@require_GET
def ajax_topics(request):
    try:
        data = api_client.get_topics(
            source_code=request.GET.get("source_code") or None,
            search=request.GET.get("search", ""),
        )
    except (BackendUnavailable, ApiError) as exc:
        return JsonResponse({"ok": False, "message": str(exc), "items": []}, status=503)
    return JsonResponse({"ok": True, "items": data})


@require_GET
def ajax_indicators(request):
    try:
        data = api_client.get_indicators(
            source_code=request.GET.get("source_code") or None,
            topic_id=_clean_int(request.GET.get("topic_id")),
            search=request.GET.get("search", ""),
            limit=250,
        )
    except (BackendUnavailable, ApiError) as exc:
        return JsonResponse({"ok": False, "message": str(exc), "items": []}, status=503)
    return JsonResponse({"ok": True, "items": data})


@require_GET
def ajax_countries(request):
    try:
        data = api_client.get_countries(search=request.GET.get("search", ""), limit=80)
    except (BackendUnavailable, ApiError) as exc:
        return JsonResponse({"ok": False, "message": str(exc), "items": []}, status=503)
    return JsonResponse({"ok": True, "items": data})


def _load_datasets() -> tuple[list[dict[str, Any]], str | None]:
    try:
        return api_client.get_published_datasets(), None
    except (BackendUnavailable, ApiError) as exc:
        return [], str(exc)


def _load_builder_catalog(
    *,
    source_code: str,
    topic_id: int | None,
    indicator_search: str,
    country_search: str,
) -> tuple[list[dict], list[dict], list[dict], list[dict], str | None]:
    try:
        sources = api_client.get_sources()
        topics = api_client.get_topics(source_code=source_code)
        indicators = api_client.get_indicators(
            source_code=source_code,
            topic_id=topic_id,
            search=indicator_search,
            limit=250,
        )
        countries = api_client.get_countries(search=country_search, limit=80)
    except (BackendUnavailable, ApiError) as exc:
        return [], [], [], [], str(exc)
    return sources, topics, indicators, countries, None


def _initial_builder_state(request, datasets: list[dict[str, Any]]) -> dict[str, Any]:
    mode = request.GET.get("mode", "new")
    existing_slug = request.GET.get("existing_slug", "")

    initial: dict[str, Any] = {
        "mode": mode if mode in {"new", "version"} else "new",
        "existing_slug": existing_slug,
        "source_code": DEFAULT_SOURCE_CODE,
        "topic_id": "",
        "indicator_ids": [],
        "country_id": "",
        "country_search": "",
        "indicator_search": "",
        "start_date": DEFAULT_START_DATE,
        "end_date": DEFAULT_END_DATE,
        "title": "",
        "description": "",
    }

    ai_recommendation = request.session.get("ai_recommendation")

    if ai_recommendation and not existing_slug:
        initial.update(
            {
                "mode": "new",
                "existing_slug": "",
                "source_code": ai_recommendation.get("source_code", DEFAULT_SOURCE_CODE),
                "topic_id": str(ai_recommendation.get("topic_id") or ""),
                "indicator_ids": [
                    str(item.get("id"))
                    for item in ai_recommendation.get("indicators", [])
                    if item.get("id")
                ],
                "country_id": str(ai_recommendation.get("country_id") or ""),
                "country_search": ai_recommendation.get("country_name", ""),
                "indicator_search": "",
                "start_date": ai_recommendation.get("start_date", DEFAULT_START_DATE),
                "end_date": ai_recommendation.get("end_date", DEFAULT_END_DATE),
                "title": ai_recommendation.get("title", ""),
                "description": ai_recommendation.get("description", ""),
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


def _build_publish_payload(cleaned: dict[str, Any]) -> dict[str, Any]:
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


def _build_timeline(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(datasets, key=lambda item: item.get("published_at") or "")
    return [
        {
            "label": str(item.get("published_at", ""))[:10] or "N/A",
            "total": index + 1,
            "height": 42 + ((index + 1) * 14),
            "title": item.get("title", ""),
        }
        for index, item in enumerate(rows[-8:])
    ]


def _manifest_value(manifest: dict[str, Any], *keys: str, default=None):
    for key in keys:
        if key in manifest:
            return manifest[key]
    return default


def _pretty_manifest(manifest: dict[str, Any]) -> str:
    import json

    return json.dumps(manifest or {}, ensure_ascii=False, indent=2)


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
