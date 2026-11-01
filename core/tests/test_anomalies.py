from datetime import date, time, timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from core.models import Attendance, AttendanceAnomaly, AttendanceAudit, WorkSchedule, Employee, AnomalyDecision
from core.services import anomalies
from .test_permissions import BaseTest


class AnomalyReviewTests(BaseTest):
    def setUp(self):
        self.item, _ = anomalies.record(self.alice, date(2026, 3, 2), "DUPLICATE_PUNCH", {"action": "check_in"})

    def test_employee_cannot_review_own_signal(self):
        with self.assertRaises(PermissionDenied):
            anomalies.decide(self.item, status="DISMISSED", note="Retry", by=self.alice_user)

    def test_review_requires_reason_and_cannot_be_repeated(self):
        with self.assertRaises(ValidationError):
            anomalies.decide(self.item, status="RESOLVED", note=" ", by=self.hr_officer)
        anomalies.decide(self.item, status="DISMISSED", note="Accidental double click", by=self.hr_officer)
        with self.assertRaises(ValidationError):
            anomalies.decide(self.item, status="RESOLVED", note="Again", by=self.hr_officer)
        self.assertEqual(AnomalyDecision.objects.get().actor, self.hr_officer)
        with self.assertRaises(Exception):
            AnomalyDecision.objects.all().delete()

    def test_repeated_detection_does_not_duplicate_or_reopen_review(self):
        anomalies.decide(self.item, status="DISMISSED", note="Retry", by=self.hr_officer)
        item, created = anomalies.record(self.alice, date(2026, 3, 2), "DUPLICATE_PUNCH", {})
        self.assertFalse(created)
        self.assertEqual(item.status, "DISMISSED")

    def test_anomaly_pages_are_permission_scoped(self):
        self.login(self.hr_officer)
        self.assertContains(self.client.get(reverse("anomaly_queue")), "not a finding of misconduct")
        self.login(self.bob_user)
        self.assertEqual(self.client.get(reverse("anomaly_review", args=[self.item.pk])).status_code, 404)


class AnomalyDetectionTests(TestCase):
    def setUp(self):
        self.schedule = WorkSchedule.objects.create(name="Office", start_time=time(9), end_time=time(17))
        self.employee = Employee.objects.create(name="Employee", basic_salary=1000,
                                                joined_on=date(2025, 1, 1), work_schedule=self.schedule)
        self.start = date(2026, 3, 2)

    def test_flags_missing_checkout_long_shift_repeated_lateness_and_brief_explanation(self):
        for offset in range(3):
            Attendance.objects.create(emp=self.employee, date=self.start + timedelta(days=offset),
                                      check_in=time(10), check_out=time(18))
        Attendance.objects.create(emp=self.employee, date=self.start + timedelta(days=3), check_in=time(9))
        record = Attendance.objects.create(emp=self.employee, date=self.start + timedelta(days=4),
                                           check_in=time(1), check_out=time(23))
        AttendanceAudit.objects.create(emp=self.employee, date=record.date, attendance=record,
                                       action="MANUAL_EDIT", reason="fix")
        self.assertEqual(anomalies.scan(self.start, self.start + timedelta(days=5)), 4)
        self.assertEqual(set(AttendanceAnomaly.objects.values_list("kind", flat=True)),
                         {"MISSING_CHECKOUT", "LONG_SHIFT", "REPEATED_LATE", "BRIEF_EXPLANATION"})
        self.assertEqual(anomalies.scan(self.start, self.start + timedelta(days=5)), 0)
        self.assertEqual(Attendance.objects.count(), 5)

    def test_scan_rejects_unbounded_or_reversed_periods(self):
        for end in (self.start - timedelta(days=1), self.start + timedelta(days=100)):
            with self.assertRaises(ValidationError):
                anomalies.scan(self.start, end)
