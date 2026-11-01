"""Shared website/bot authorization and atomic organization fan-out."""
import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.organizations.models import OrganizationMembership
from apps.organizations.tenancy import scope_queryset
from core.models import Announcement, Notification, OrganizationInvitation, WhatsAppLink


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require_organization(user, organization, permission):
    if not user.is_active or not user.has_perm(permission) or not organization.is_active:
        raise PermissionDenied
    if not user.is_superuser and not OrganizationMembership.objects.filter(
            user=user, organization=organization, is_active=True).exists():
        raise PermissionDenied


@transaction.atomic
def announce(*, user, organization, title, message):
    require_organization(user, organization, 'core.send_announcement')
    title, message = title.strip(), message.strip()
    if not title or not message or len(title) > 150 or len(message) > 4000:
        raise ValidationError('Provide a title (up to 150 characters) and message (up to 4000 characters).')
    announcement = Announcement.objects.create(organization=organization, author=user, title=title, message=message)
    recipients = get_user_model().objects.filter(is_active=True,
        organization_memberships__organization=organization,
        organization_memberships__is_active=True).distinct().values_list('pk', flat=True)
    from itertools import islice
    recipient_ids = recipients.iterator(chunk_size=500)
    while batch := list(islice(recipient_ids, 500)):
        Notification.objects.bulk_create([
            Notification(organization=organization, recipient_id=pk, notification_type=Notification.ANNOUNCEMENT,
                         title=title, message=message, unique_key=f'announcement:{announcement.pk}')
            for pk in batch
        ], batch_size=500)
    return announcement


def visible_notifications(user):
    from django.db.models import Q
    from apps.organizations.tenancy import organization_ids_for
    return Notification.objects.filter(recipient=user).filter(
        Q(organization__isnull=True) | Q(organization_id__in=organization_ids_for(user)))


@transaction.atomic
def invite(*, user, organization, email):
    require_organization(user, organization, 'core.link_employee_account')
    token = secrets.token_urlsafe(32)
    OrganizationInvitation.objects.create(organization=organization, email=email.strip().lower(),
        token_hash=digest(token), expires_at=timezone.now() + timedelta(days=2), created_by=user)
    return token


@transaction.atomic
def create_link_token(user):
    token = secrets.token_urlsafe(24)
    link, _ = WhatsAppLink.objects.select_for_update().get_or_create(user=user)
    link.token_hash, link.expires_at = digest(token), timezone.now() + timedelta(minutes=10)
    link.save(update_fields=['token_hash', 'expires_at'])
    return token


def employee_for_bot(user):
    from core.models import Employee
    if not user.is_active:
        raise PermissionDenied
    employee = scope_queryset(Employee.objects.select_related('organization', 'user'), user).filter(
        user=user, is_active=True, organization__is_active=True).first()
    if not employee:
        raise PermissionDenied
    from core.services.accounts import must_change_password
    if must_change_password(user):
        raise PermissionDenied
    return employee
