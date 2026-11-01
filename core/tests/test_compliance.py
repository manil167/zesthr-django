from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from core.models import AttendanceAudit
from .test_permissions import BaseTest


class ComplianceReportTests(BaseTest):
    def setUp(self):
        AttendanceAudit.objects.create(emp=self.alice, date=timezone.localdate(),
            action="MANUAL_EDIT", reason="Explained correction", changed_by=self.hr_officer)

    def test_report_and_export_require_audit_permission(self):
        self.login(self.alice_user)
        self.assertEqual(self.client.get(reverse("compliance_report")).status_code, 403)
        self.login(self.hr_admin)
        self.assertContains(self.client.get(reverse("compliance_report")), "Explained correction")
        response = self.client.get(reverse("compliance_report"), {"export": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Explained correction", response.content.decode())

    def test_invalid_dates_are_shown_as_form_errors(self):
        self.login(self.hr_admin)
        response = self.client.get(reverse("compliance_report"),
            {"source": "attendance", "start": "invalid", "end": timezone.localdate().isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)

    def test_export_neutralizes_formula_text(self):
        AttendanceAudit.objects.create(emp=self.alice, date=timezone.localdate(),
                                       action="MANUAL_EDIT", reason="=HYPERLINK(unsafe)")
        self.login(self.hr_admin)
        response = self.client.get(reverse("compliance_report"), {"export": "csv"})
        self.assertIn("'=HYPERLINK", response.content.decode())
