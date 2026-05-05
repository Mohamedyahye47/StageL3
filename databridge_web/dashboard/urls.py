from __future__ import annotations

from django.urls import path

from . import views


app_name = "dashboard"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/", views.dashboard, name="dashboard_alias"),
    path("datasets/", views.dataset_list, name="dataset_list"),
    path("datasets/create/", views.dataset_create, name="dataset_create"),
    path("datasets/<slug:slug>/", views.dataset_detail, name="dataset_detail"),
    path("health/", views.health, name="health"),
    path("settings/", views.health, name="settings"),
    path("ajax/topics/", views.ajax_topics, name="ajax_topics"),
    path("ajax/indicators/", views.ajax_indicators, name="ajax_indicators"),
    path("ajax/countries/", views.ajax_countries, name="ajax_countries"),
    path("assistant/", views.ai_assistant, name="ai_assistant"),
]
