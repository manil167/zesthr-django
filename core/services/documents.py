"""Private employee document validation, access control, and retention."""
import hashlib
from io import BytesIO
from pathlib import Path
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from pypdf import PdfReader
from pypdf.generic import IndirectObject
from apps.organizations.tenancy import scope_queryset
from .. import roles
from ..models import Employee, EmployeeDocument, DocumentEvent

MAX_BYTES = 10 * 1024 * 1024
FORBIDDEN_KEYS = {"/JavaScript", "/JS", "/OpenAction", "/AA", "/Launch", "/EmbeddedFiles", "/XFA", "/RichMediaContent"}


def authorize_employee(employee, user, *, edit=False):
    if not user.is_authenticated or not scope_queryset(Employee.objects.filter(pk=employee.pk), user).exists():
        raise PermissionDenied
    hr = user.has_perm(roles.VIEW_EMPLOYEE_PERSONAL)
    if edit:
        if not hr or not user.has_perm(roles.CHANGE_EMPLOYEE):
            raise PermissionDenied
    elif not hr and employee.user_id != user.pk:
        raise PermissionDenied
    return hr


def validate_pdf(upload):
    if Path(upload.name).suffix.lower() != ".pdf":
        raise ValidationError("Upload a PDF document.")
    content = upload.read(MAX_BYTES + 1)
    upload.seek(0)
    if not content or len(content) > MAX_BYTES:
        raise ValidationError("PDF documents must be between 1 byte and 10 MB.")
    if not content.startswith(b"%PDF-"):
        raise ValidationError("The uploaded file is not a PDF.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
            raise ValueError("Encrypted or oversized PDF")
        seen, pending, inspected = set(), [reader.trailer], 0
        while pending:
            value = pending.pop()
            if isinstance(value, IndirectObject):
                key = (value.idnum, value.generation)
                if key in seen:
                    continue
                seen.add(key)
                value = value.get_object()
            inspected += 1
            if inspected > 20000:
                raise ValueError("PDF object limit exceeded")
            if isinstance(value, dict):
                if FORBIDDEN_KEYS.intersection(value):
                    raise ValueError("Active PDF content")
                pending.extend(value.values())
            elif isinstance(value, (list, tuple)):
                pending.extend(value)
    except Exception as exc:
        raise ValidationError("Use a valid, unencrypted PDF without scripts or embedded files (maximum 500 pages).") from exc
    return len(content), hashlib.sha256(content).hexdigest()


@transaction.atomic
def upload_document(employee, *, upload, title, document_type, employee_visible=False, retain_until=None, by):
    authorize_employee(employee, by, edit=True)
    if not settings.DOCUMENT_UPLOADS_ENABLED:
        raise ValidationError("Document uploads are disabled until private storage is configured.")
    size, digest = validate_pdf(upload)
    document = EmployeeDocument(employee=employee, title=title.strip(), document_type=document_type,
        original_name=Path(upload.name).name[:255], sha256=digest, byte_size=size,
        employee_visible=employee_visible, retain_until=retain_until, uploaded_by=by)
    document.full_clean(exclude=["file"])
    document.file.save(upload.name, upload, save=False)
    try:
        document.save()
        DocumentEvent.objects.create(document=document, action="UPLOADED", actor=by)
    except Exception:
        document.file.storage.delete(document.file.name)
        raise
    return document


def visible_documents(employee, user):
    hr = authorize_employee(employee, user)
    queryset = employee.documents.filter(purged_at__isnull=True, purge_requested_at__isnull=True)
    return queryset if hr else queryset.filter(employee_visible=True)


@transaction.atomic
def request_purge(document, *, by, reason):
    document = EmployeeDocument.objects.select_for_update().select_related("employee").get(pk=document.pk)
    authorize_employee(document.employee, by, edit=True)
    if not document.retain_until or document.retain_until >= timezone.localdate():
        raise ValidationError("The retention period has not expired; this document must be retained.")
    if not reason.strip():
        raise ValidationError("Explain the deletion request.")
    if document.purge_requested_at or document.purged_at:
        return document
    document.purge_requested_at = timezone.now()
    document.save(update_fields=["purge_requested_at"])
    DocumentEvent.objects.create(document=document, action="PURGE_REQUESTED", actor=by, reason=reason.strip()[:255])
    return document


def process_purge(document_id):
    """Retry-safe worker: a deleted object can be deleted again after a DB failure."""
    with transaction.atomic():
        document = EmployeeDocument.objects.select_for_update().get(pk=document_id)
        if document.purged_at or not document.purge_requested_at:
            return False
        if not document.retain_until or document.retain_until >= timezone.localdate():
            return False
        document.file.storage.delete(document.file.name)
        document.file = ""
        document.purged_at = timezone.now()
        document.save(update_fields=["file", "purged_at"])
        DocumentEvent.objects.create(document=document, action="PURGED", reason="Expired retention; authorized deletion request")
    return True
