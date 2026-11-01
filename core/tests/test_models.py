from datetime import date, time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import Attendance, Employee, LeaveApplication


class ModelTests(TestCase):
    def setUp(self):
        self.emp = Employee.objects.create(name="Ann", designation="Dev", basic_salary=Decimal("30000"))

    def test_attendance_hours(self):
        a = Attendance.objects.create(emp=self.emp, date=date(2026, 1, 5), check_in=time(9, 0), check_out=time(17, 30))
        self.assertEqual(a.hours, 8.5)
        self.assertIsNone(Attendance(emp=self.emp, date=date(2026, 1, 6), check_in=time(9, 0)).hours)

    def test_one_attendance_row_per_employee_per_day(self):
        Attendance.objects.create(emp=self.emp, date=date(2026, 1, 5))
        with self.assertRaises(Exception):
            Attendance.objects.create(emp=self.emp, date=date(2026, 1, 5))

    def test_leave_days_count_is_inclusive(self):
        leave = LeaveApplication(emp=self.emp, start_date=date(2026, 3, 2), end_date=date(2026, 3, 4))
        self.assertEqual(leave.days_count, 3)

    def test_sick_leave_balance(self):
        today = timezone.localdate()
        self.assertEqual(self.emp.sick_leaves_remaining(today.month, today.year), 1)
        LeaveApplication.objects.create(emp=self.emp, leave_type="SL", status="APPROVED",
                                        start_date=today, end_date=today, reason="x")
        self.assertEqual(self.emp.sick_leaves_remaining(today.month, today.year), 0)
        # A pending request does not use the quota yet.
        other = Employee.objects.create(name="Ben", designation="QA", basic_salary=Decimal("1"))
        LeaveApplication.objects.create(emp=other, leave_type="SL", start_date=today, end_date=today, reason="x")
        self.assertEqual(other.sick_leaves_remaining(today.month, today.year), 1)
