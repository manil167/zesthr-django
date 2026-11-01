from django.conf import settings
from django.db import models
from .models import AppendOnlyModel
from .storage import PrivateDocumentStorage, document_path


class EmployeeDocument(models.Model):
    TYPES = [("LETTER", "Employment letter"), ("CONTRACT", "Contract"),
             ("FORM", "Approved HR form"), ("OTHER", "Other employment document")]
    employee = models.ForeignKey("core.Employee", on_delete=models.PROTECT, related_name="documents")
    title = models.CharField(max_length=150)
    document_type = models.CharField(max_length=10, choices=TYPES)
    file = models.FileField(storage=PrivateDocumentStorage(), upload_to=document_path, blank=True)
    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveIntegerField()
    employee_visible = models.BooleanField(default=False)
    retain_until = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    purge_requested_at = models.DateTimeField(null=True, blank=True)
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at", "-pk"]
        indexes = [models.Index(fields=["employee", "uploaded_at"], name="employee_document_list_idx")]


class DocumentEvent(AppendOnlyModel):
    document = models.ForeignKey(EmployeeDocument, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
