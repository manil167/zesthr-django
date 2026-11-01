from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.db.models.signals import post_save

from .models import SecurityEvent
from .services import accounts


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    accounts.record_event(SecurityEvent.LOGIN_SUCCESS, request=request, user=user)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        accounts.record_event(SecurityEvent.LOGOUT, request=request, user=user)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    # Only the name that was typed is kept; the password is never read.
    accounts.record_event(SecurityEvent.LOGIN_FAILED, request=request, username=credentials.get("username", ""))


@receiver(post_save, sender='core.Employee')
def ensure_employee_membership(sender, instance, **kwargs):
    if instance.user_id and instance.organization_id:
        from apps.organizations.models import OrganizationMembership
        OrganizationMembership.objects.get_or_create(user_id=instance.user_id,
                                                     organization_id=instance.organization_id)
