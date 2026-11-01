from django.conf import settings
from django.db import models
from .models import AppendOnlyModel


class AttendanceAnomaly(models.Model):
    KINDS = [("MISSING_CHECKOUT", "Missing check-out"), ("REPEATED_LATE", "Repeated late arrivals"),
             ("DUPLICATE_PUNCH", "Repeated attendance submission"), ("LONG_SHIFT", "Unusually long shift"),
             ("BRIEF_EXPLANATION", "Explanation needs review")]
    STATUSES = [("OPEN", "Open"), ("RESOLVED", "Resolved"), ("DISMISSED", "Dismissed")]
    employee = models.ForeignKey("core.Employee", on_delete=models.PROTECT, related_name="attendance_anomalies")
    date = models.DateField()
    kind = models.CharField(max_length=25, choices=KINDS)
    fingerprint = models.CharField(max_length=64, unique=True)
    evidence = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=STATUSES, default="OPEN")
    detected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]
        indexes = [models.Index(fields=["status", "date"], name="anomaly_review_queue_idx")]


class AnomalyDecision(AppendOnlyModel):
    anomaly = models.ForeignKey(AttendanceAnomaly, on_delete=models.PROTECT, related_name="decisions")
    status = models.CharField(max_length=10, choices=AttendanceAnomaly.STATUSES)
    note = models.CharField(max_length=255)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
