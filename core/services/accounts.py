"""Account lifecycle and sign-in security.

Activation: an account is active or not (`User.is_active`). An inactive account cannot
sign in, and an existing session stops working on its next request, so access can be
revoked immediately. Deactivating an employee deactivates their login (see employees.py).

Provisioned accounts (created by an administrator) must choose their own password at first
sign-in. Repeated failed sign-ins temporarily block further attempts for that name + address.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import AccountSecurity, SecurityEvent


def client_ip(request):
    """Caller's address. Only trusts X-Forwarded-For when explicitly configured, because
    a client can forge that header unless your own proxy overwrites it."""
    if request is None:
        return None
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[-1].strip() or None  # the hop added by our own proxy
    return request.META.get("REMOTE_ADDR") or None


def record_event(event_type, *, request=None, user=None, username="", detail=""):
    meta = request.META if request is not None else {}
    return SecurityEvent.objects.create(
        user=user if getattr(user, "pk", None) else None,
        username=(username or getattr(user, "username", "") or "")[:150],
        event_type=event_type, ip_address=client_ip(request),
        user_agent=meta.get("HTTP_USER_AGENT", "")[:255], detail=detail[:255])


# --- sign-in throttling ---------------------------------------------------------------
def recent_failures(username, ip):
    """Failed sign-ins for this name from this address since the window began or the last success."""
    window_start = timezone.now() - timedelta(minutes=settings.LOGIN_FAILURE_WINDOW_MINUTES)
    events = SecurityEvent.objects.filter(username__iexact=username, ip_address=ip, created_at__gte=window_start)
    last_ok = events.filter(event_type=SecurityEvent.LOGIN_SUCCESS).order_by("-created_at").first()
    failures = events.filter(event_type=SecurityEvent.LOGIN_FAILED)
    if last_ok:
        failures = failures.filter(created_at__gt=last_ok.created_at)
    return failures.count()


def is_login_blocked(username, ip):
    return bool(username) and recent_failures(username, ip) >= settings.LOGIN_MAX_FAILURES


# --- password reset throttling -------------------------------------------------------
def is_password_reset_blocked(identifier, ip):
    """Limit reset-email requests without revealing whether an account exists."""
    if not identifier:
        return False
    window_start = timezone.now() - timedelta(minutes=settings.PASSWORD_RESET_WINDOW_MINUTES)
    requests = SecurityEvent.objects.filter(
        username__iexact=identifier,
        ip_address=ip,
        event_type=SecurityEvent.PASSWORD_RESET_REQUESTED,
        created_at__gte=window_start,
    )
    return requests.count() >= settings.PASSWORD_RESET_MAX_REQUESTS


# --- password state -------------------------------------------------------------------
def must_change_password(user):
    return AccountSecurity.objects.filter(user=user, must_change_password=True).exists()


@transaction.atomic
def require_password_change(user, *, by=None):
    sec, _ = AccountSecurity.objects.get_or_create(user=user)
    sec.must_change_password = True
    sec.save(update_fields=["must_change_password"])
    record_event(SecurityEvent.PASSWORD_CHANGE_REQUIRED, user=user, detail=f"set by {by}" if by else "")


@transaction.atomic
def mark_password_changed(user):
    sec, _ = AccountSecurity.objects.get_or_create(user=user)
    sec.must_change_password = False
    sec.password_changed_at = timezone.now()
    sec.save(update_fields=["must_change_password", "password_changed_at"])


# --- activation -----------------------------------------------------------------------
@transaction.atomic
def set_account_active(user, active, *, by=None):
    if user.is_active == active:
        return user
    user.is_active = active
    user.save(update_fields=["is_active"])
    record_event(SecurityEvent.ACCOUNT_ACTIVATED if active else SecurityEvent.ACCOUNT_DEACTIVATED,
                 user=user, detail=f"by {by}" if by else "")
    return user
