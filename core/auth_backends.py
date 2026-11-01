from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db import transaction, IntegrityError

from core.models import NeonIdentity
from core.services import neon_auth


class NeonBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not neon_auth.enabled() or not request or not username or not password:
            return None
        try:
            _, cookie = neon_auth.call('sign-in/email', payload={'email': username, 'password': password})
            if not cookie:
                raise neon_auth.NeonAuthError('No authentication session was returned.')
            profile = neon_auth.session_user(cookie)
            user = provision(profile)
            if not self.user_can_authenticate(user):
                return None
            request.session['neon_cookie'] = cookie
            request.session['neon_subject'] = str(profile['id'])
            return user
        except neon_auth.NeonAuthError:
            return None


@transaction.atomic
def provision(profile):
    """Never match an existing privileged/local account using its email address."""
    from django.conf import settings
    import hashlib
    issuer, subject = settings.NEON_AUTH_BASE_URL.rstrip('/'), str(profile['id'])
    identity = NeonIdentity.objects.select_related('user').filter(issuer=issuer, subject=subject).first()
    if identity:
        identity.user.email = str(profile.get('email', ''))[:254]
        identity.user.save(update_fields=['email'])
        grant_configured_neon_admin(identity.user, profile)
        return identity.user
    # Unique provider identity is the account key; email alone never grants access.
    username = 'neon_' + hashlib.sha256((issuer + ':' + subject).encode()).hexdigest()[:40]
    user = get_user_model()(username=username, email=str(profile.get('email', ''))[:254],
                           first_name=str(profile.get('name', ''))[:150])
    user.set_unusable_password()
    try:
        with transaction.atomic():
            user.save()
            NeonIdentity.objects.create(user=user, issuer=issuer, subject=subject)
        grant_configured_neon_admin(user, profile)
        return user
    except IntegrityError:
        # A concurrent login may have provisioned the same verified identity.
        identity = NeonIdentity.objects.select_related('user').filter(issuer=issuer, subject=subject).first()
        if identity:
            grant_configured_neon_admin(identity.user, profile)
            return identity.user
        # A colliding local username is never adopted by email or by username.
        raise neon_auth.NeonAuthError('Account provisioning requires administrator assistance.') from None


def grant_configured_neon_admin(user, profile):
    """Trusted deployment configuration authorizes one exact verified provider ID.

    No existing local account is linked by its email, password or username.
    """
    import os
    if os.environ.get('DJANGO_BOOTSTRAP_ADMIN', '').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        return
    subject = os.environ.get('NEON_ADMIN_USER_ID', '').strip()
    email = os.environ.get('NEON_ADMIN_EMAIL', '').strip()
    if (not subject or not email or str(profile.get('id')) != subject
            or not profile.get('emailVerified')
            or str(profile.get('email', '')).strip().casefold() != email.casefold()
            or not user.is_active):
        return
    if not (user.is_superuser and user.is_staff):
        user.is_superuser, user.is_staff = True, True
        user.save(update_fields=['is_superuser', 'is_staff'])
