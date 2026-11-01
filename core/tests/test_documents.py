from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfWriter
from core.models import Employee, EmployeeDocument, DocumentEvent
from core.services import documents


def pdf(name="contract.pdf", *, script=False):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    if script:
        writer.add_js("app.alert('active');")
    buffer = BytesIO()
    writer.write(buffer)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="application/pdf")


class PrivateDocumentTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(DOCUMENT_UPLOADS_ENABLED=True, STORAGES={
            **settings.STORAGES, "private_documents": {"BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": self.directory.name, "base_url": None}}})
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.hr = User.objects.create_superuser("hr")
        self.user = User.objects.create_user("employee")
        self.other = User.objects.create_user("other")
        self.employee = Employee.objects.create(name="Employee", basic_salary=1000, user=self.user)
        Employee.objects.create(name="Other", basic_salary=1000, user=self.other)

    def upload(self, **extra):
        values = dict(upload=pdf(), title="Contract", document_type="CONTRACT", by=self.hr)
        values.update(extra)
        return documents.upload_document(self.employee, **values)

    def test_document_is_stored_privately_and_audited(self):
        document = self.upload()
        self.assertEqual(len(document.sha256), 64)
        self.assertNotIn("contract.pdf", document.file.name)
        self.assertTrue(document.file.storage.exists(document.file.name))
        with self.assertRaises(ValueError):
            document.file.url
        self.assertEqual(document.events.get().action, "UPLOADED")

    def test_invalid_and_active_pdfs_are_rejected_before_storage(self):
        for upload in (pdf("contract.exe"), pdf(script=True),
                       SimpleUploadedFile("fake.pdf", b"%PDF-this is not valid"),
                       SimpleUploadedFile("large.pdf", b"x" * (documents.MAX_BYTES + 1))):
            with self.subTest(name=upload.name), self.assertRaises(ValidationError):
                self.upload(upload=upload)
        self.assertFalse(EmployeeDocument.objects.exists())

    def test_employee_sees_only_explicitly_shared_documents(self):
        hidden = self.upload()
        shared = self.upload(employee_visible=True)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("document_download", args=[hidden.pk])).status_code, 404)
        response = self.client.get(reverse("document_download", args=[shared.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(response["Cache-Control"], "private, no-store")
        content = b"".join(response.streaming_content)
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertTrue(shared.events.filter(action="DOWNLOADED", actor=self.user).exists())

    def test_cross_employee_download_is_rejected(self):
        document = self.upload(employee_visible=True)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("document_download", args=[document.pk])).status_code, 404)

    def test_employee_cannot_upload_for_self(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("employee_documents", args=[self.employee.pk]),
                                    {"title": "Forged", "document_type": "CONTRACT", "upload": pdf()})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EmployeeDocument.objects.exists())

    def test_retention_protects_document_from_early_deletion(self):
        document = self.upload(retain_until=timezone.localdate() + timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, "retention"):
            documents.request_purge(document, by=self.hr, reason="Delete")
        self.assertTrue(document.file.storage.exists(document.file.name))

    def test_requested_expired_deletion_is_retry_safe_and_audited(self):
        document = self.upload(retain_until=timezone.localdate() - timedelta(days=1))
        documents.request_purge(document, by=self.hr, reason="Retention completed")
        self.assertTrue(documents.process_purge(document.pk))
        self.assertFalse(documents.process_purge(document.pk))
        document.refresh_from_db()
        self.assertFalse(document.file)
        self.assertIsNotNone(document.purged_at)
        self.assertEqual(document.events.count(), 3)
        with self.assertRaises(Exception):
            DocumentEvent.objects.all().delete()

    def test_disabled_storage_rejects_upload(self):
        with override_settings(DOCUMENT_UPLOADS_ENABLED=False), self.assertRaisesMessage(ValidationError, "disabled"):
            self.upload()
