from __future__ import annotations

from django.urls import path
from django.views.generic import RedirectView

from . import views


app_name = "dashboard"

urlpatterns = [
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("setup/first-admin/", views.first_admin_setup, name="first_admin_setup"),
    path("settings/users/", views.superuser_management, name="superuser_management"),
    path("healthz/", views.healthz, name="healthz"),
    path("charts/export-chronology.png", views.export_chronology_chart, name="export_chronology_chart"),
    path("", views.dashboard, name="dashboard"),
    path("dashboard/", views.dashboard, name="dashboard_alias"),
    path("datasets/", views.dataset_list, name="dataset_list"),
    path("datasets/create/", views.dataset_create, name="dataset_create"),
    path("datasets/<slug:slug>/", views.dataset_detail, name="dataset_detail"),
    path("dataset/create/", views.dataset_create, name="dataset_create_alias"),
    path("dataset/<slug:slug>/", views.dataset_detail, name="dataset_detail_alias"),
    path("model-parameters/", views.model_parameters, name="model_parameters"),
    path("rules/", RedirectView.as_view(pattern_name="dashboard:dashboard", permanent=False), name="creation_rules"),
    path("settings/", views.model_parameters, name="settings"),
    path("ajax/topics/", views.ajax_topics, name="ajax_topics"),
    path("ajax/indicators/", views.ajax_indicators, name="ajax_indicators"),
    path("ajax/countries/", views.ajax_countries, name="ajax_countries"),
    path("ajax/datasets/preview/", views.ajax_dataset_preview, name="ajax_dataset_preview"),
    path("ajax/datasets/<slug:slug>/preview/", views.ajax_dataset_detail_preview, name="ajax_dataset_detail_preview"),
    path("assistant/", views.ai_assistant, name="ai_assistant"),
    path("ai-assistant/", views.ai_assistant, name="ai_assistant_alias"),
]
