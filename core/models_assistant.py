"""Bounded per-user AI usage; no prompts or model output are stored here."""
from django.conf import settings
from django.db import models


class AssistantQuota(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    minute = models.DateTimeField()
    minute_count = models.PositiveIntegerField(default=0)
    day = models.DateField()
    day_count = models.PositiveIntegerField(default=0)
