"""Tenant announcements and verified WhatsApp identities with a durable outbox."""
from django.conf import settings
from django.db import models


class Announcement(models.Model):
    organization = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=150)
    message = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [('send_announcement', 'Send organization announcements')]
        ordering = ['-created_at']


class OrganizationInvitation(models.Model):
    organization = models.ForeignKey('organizations.Organization', on_delete=models.PROTECT)
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class WhatsAppLink(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    token_hash = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    linked_at = models.DateTimeField(null=True, blank=True)


class WhatsAppReceipt(models.Model):
    message_id = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class WhatsAppOutbox(models.Model):
    phone = models.CharField(max_length=20)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    last_error = models.CharField(max_length=100, blank=True)

    class Meta:
        indexes = [models.Index(fields=['sent_at', 'next_attempt_at'], name='whatsapp_outbox_due')]


class NeonIdentity(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    issuer = models.URLField(max_length=500)
    subject = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['issuer', 'subject'], name='neon_issuer_subject_unique')]
