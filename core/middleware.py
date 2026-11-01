from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from .services import accounts


class ForcePasswordChangeMiddleware:
    """Signed-in users flagged `must_change_password` can only change it or sign out."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and accounts.must_change_password(user):
            allowed = (reverse("password_change"), reverse("logout"))
            if request.path not in allowed and not request.path.startswith(settings.STATIC_URL):
                messages.warning(request, "Please choose a new password before continuing.")
                return redirect("password_change")
        return self.get_response(request)


class NeonSessionMiddleware:
    """Revalidate remote sessions; provider outages fail closed for protected pages."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.session.get('_auth_user_backend') == 'core.auth_backends.NeonBackend':
            from django.contrib.auth import logout
            from core.services import neon_auth
            from core.models import NeonIdentity
            try:
                profile = neon_auth.session_user(request.session.get('neon_cookie', ''))
                valid = NeonIdentity.objects.filter(user=request.user, issuer=settings.NEON_AUTH_BASE_URL.rstrip('/'),
                                                    subject=str(profile['id'])).exists()
                if not valid:
                    raise neon_auth.NeonAuthError('Session identity changed.')
            except neon_auth.NeonAuthError:
                logout(request)
                return redirect('login')
        return self.get_response(request)


class TenantScopeMiddleware:
    """Cache memberships for this HTTP request only, never on long-lived user objects."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            from apps.organizations.tenancy import organization_ids_for
            request.user._hros_request_organization_ids = list(organization_ids_for(request.user))
        return self.get_response(request)
