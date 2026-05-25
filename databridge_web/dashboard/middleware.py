from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render
from django.urls import reverse

from .audit import record_audit_event


class RequireSuperuserLoginMiddleware:
    """Restrict the UI to authenticated Django superusers only."""

    PUBLIC_PREFIXES = (
        "/accounts/login/",
        "/accounts/logout/",
        "/setup/first-admin/",
        "/healthz/",
        "/favicon.ico",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info or "/"
        if self._is_public(path):
            return self.get_response(request)

        User = get_user_model()
        if not User.objects.filter(is_superuser=True).exists():
            return redirect("dashboard:first_admin_setup")

        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            login_url = reverse("dashboard:login")
            return redirect(f"{login_url}?next={request.get_full_path()}")

        if not getattr(user, "is_superuser", False):
            record_audit_event(
                request,
                action="access_denied_non_superuser",
                object_type="django_route",
                object_id=path,
            )
            return render(request, "403.html", status=403)

        return self.get_response(request)

    def _is_public(self, path: str) -> bool:
        static_url = getattr(settings, "STATIC_URL", "/static/")
        if static_url and path.startswith(static_url):
            return True
        return any(path == prefix or path.startswith(prefix) for prefix in self.PUBLIC_PREFIXES)
