from datetime import date, time

from django.test import TestCase
from django.urls import reverse

from core.models import Attendance, Employee
from core.services import attendance as svc

from .test_permissions import BaseTest

TODAY = date(2026, 4, 1)


class AttendanceServiceTests(TestCase):
    def setUp(self):
        self.emp = Employee.objects.create(name="Ann", designation="Dev", basic_salary=1000)

    def test_check_in_creates_present_record(self):
        rec = svc.check_in(self.emp, now=time(9, 5), today=TODAY)
        self.assertEqual((rec.status, rec.check_in, rec.date), ("P", time(9, 5), TODAY))

    def test_check_out_records_time_on_same_row(self):
        svc.check_in(self.emp, now=time(9, 0), today=TODAY)
        svc.check_out(self.emp, now=time(17, 0), today=TODAY)
        self.assertEqual(Attendance.objects.filter(emp=self.emp).count(), 1)
        self.assertEqual(Attendance.objects.get(emp=self.emp).check_out, time(17, 0))

    def test_absent_and_leave_clear_punch_times_but_half_day_keeps_them(self):
        svc.check_in(self.emp, now=time(9, 0), today=TODAY)
        self.assertEqual(svc.mark_status(self.emp, "H", today=TODAY).check_in, time(9, 0))
        rec = svc.mark_status(self.emp, "A", today=TODAY, reason="did not actually attend")
        self.assertEqual((rec.status, rec.check_in, rec.check_out), ("A", None, None))
        self.assertEqual(svc.mark_status(self.emp, "L", today=TODAY).status, "L")

    def test_unsupported_status_is_rejected_and_nothing_is_created(self):
        for bad in ("P", "X", "", None):
            with self.assertRaises(ValueError):
                svc.mark_status(self.emp, bad, today=TODAY)
        self.assertFalse(Attendance.objects.exists())


class UnknownActionTests(BaseTest):
    """Regression: an unknown action used to create a 'Present' record with no punch."""

    def test_unknown_action_creates_nothing(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("attendance_today"), {"action": "bogus"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Attendance.objects.filter(emp=self.alice, date=date.today()).exists())
