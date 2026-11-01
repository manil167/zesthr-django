from datetime import date
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from core.models import Attendance, Employee, LeaveDecision, Notification
from core.services import leave, attendance


class LeaveDecisionTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_superuser("reviewer")
        self.user = User.objects.create_user("employee")
        self.emp = Employee.objects.create(name="Employee", basic_salary=1000, user=self.user, joined_on=date(2025, 1, 1))
        self.day = date(2026, 3, 2)

    def request(self, **kwargs):
        return leave.submit_leave(self.emp, leave_type=kwargs.get("leave_type", "CL"),
                                  start_date=self.day, end_date=kwargs.get("end_date", self.day), reason="Family")

    def test_pending_requests_reserve_dates(self):
        self.request()
        with self.assertRaisesMessage(ValidationError, "overlaps"):
            self.request(leave_type="PL")

    def test_pending_sick_leave_reserves_monthly_quota(self):
        self.request(leave_type="SL")
        self.day = date(2026, 3, 20)
        with self.assertRaisesMessage(ValidationError, "March 2026"):
            self.request(leave_type="SL")

    def test_approval_is_idempotent_with_one_decision_and_one_notification(self):
        request = self.request()
        leave.approve_leave(request, by=self.reviewer)
        leave.approve_leave(request, by=self.reviewer)
        self.assertEqual(LeaveDecision.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.user).count(), 1)
        self.assertEqual(self.emp.attendance.get().status, "L")
        self.assertTrue(self.emp.attendance_audit.exists())

    def test_decided_request_cannot_be_changed_to_conflicting_status(self):
        request = self.request()
        leave.approve_leave(request, by=self.reviewer)
        with self.assertRaises(ValidationError):
            leave.reject_leave(request, by=self.reviewer)
        self.assertEqual(self.emp.attendance.get().status, "L")

    def test_employee_cannot_approve_own_request_even_with_admin_rights(self):
        self.user.is_superuser = True
        self.user.save()
        with self.assertRaises(PermissionDenied):
            leave.approve_leave(self.request(), by=self.user)

    def test_decision_cannot_be_changed_or_deleted(self):
        leave.approve_leave(self.request(), by=self.reviewer)
        decision = LeaveDecision.objects.get()
        with self.assertRaises(Exception):
            decision.delete()
        with self.assertRaises(Exception):
            LeaveDecision.objects.filter(pk=decision.pk).update(note="Tampered")

    def test_cancel_restores_previous_attendance(self):
        Attendance.objects.create(emp=self.emp, date=self.day, status="A")
        request = self.request()
        leave.approve_leave(request, by=self.reviewer)
        leave.cancel_leave(request, by=self.reviewer, note="Approved in error")
        self.assertEqual(self.emp.attendance.get().status, "A")
        request.refresh_from_db()
        self.assertEqual(request.status, "CANCELLED")

    def test_cancel_removes_only_attendance_created_by_approval(self):
        request = self.request()
        leave.approve_leave(request, by=self.reviewer)
        leave.cancel_leave(request, by=self.reviewer, note="Dates changed")
        self.assertFalse(self.emp.attendance.exists())
        self.assertTrue(self.emp.attendance_audit.filter(action="MANUAL_DELETE").exists())

    def test_cancel_does_not_overwrite_later_corrections(self):
        request = self.request()
        leave.approve_leave(request, by=self.reviewer)
        attendance.save_manual(self.emp, self.day, status="A", by=self.reviewer, reason="Reviewed")
        with self.assertRaisesMessage(ValidationError, "Attendance changed"):
            leave.cancel_leave(request, by=self.reviewer, note="Dates changed")
        self.assertEqual(self.emp.attendance.get().status, "A")

    def test_employee_can_cancel_pending_request_with_reason(self):
        request = self.request()
        leave.cancel_leave(request, by=self.user, note="No longer needed")
        self.assertFalse(self.emp.attendance.exists())
        self.assertEqual(request.decisions.get().decided_by, self.user)
