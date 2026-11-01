from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from core.models import Attendance, Employee, Salary
from core.services import payroll as svc


def emp(name, salary, **kw):
    return Employee.objects.create(name=name, designation="x", basic_salary=Decimal(salary), **kw)


def mark(e, y, m, d, status):
    Attendance.objects.create(emp=e, date=date(y, m, d), status=status)


class PayrollCalculationTests(TestCase):
    def row(self, e, month, year):
        return next(r for r in svc.calculate_payroll_rows(month, year) if r["emp"].pk == e.pk)

    def test_absent_and_half_days_are_deducted_per_calendar_day(self):
        e = emp("A", "31000")  # January has 31 days -> 1000 per day
        for d in (1, 2):
            mark(e, 2026, 1, d, "A")
        mark(e, 2026, 1, 3, "H")
        r = self.row(e, 1, 2026)
        self.assertEqual((r["per_day"], r["absent_days"], r["deduction"], r["net"]),
                         (Decimal("1000.00"), Decimal("2.5"), Decimal("2500.00"), Decimal("28500.00")))

    def test_uses_the_real_length_of_the_month(self):
        e = emp("A", "28000")
        self.assertEqual(self.row(e, 2, 2026)["per_day"], Decimal("1000.00"))  # 28 days
        self.assertEqual(self.row(e, 2, 2024)["per_day"], Decimal("965.52"))   # leap year, 29 days

    def test_rounding_is_to_the_cent(self):
        e = emp("A", "10000")  # 30-day month -> 333.333...
        mark(e, 2026, 4, 1, "A")
        r = self.row(e, 4, 2026)
        self.assertEqual((r["per_day"], r["deduction"], r["net"]),
                         (Decimal("333.33"), Decimal("333.33"), Decimal("9666.67")))

    def test_leave_and_present_days_cost_nothing(self):
        e = emp("A", "30000")
        mark(e, 2026, 4, 1, "L")
        mark(e, 2026, 4, 2, "P")
        r = self.row(e, 4, 2026)
        self.assertEqual((r["deduction"], r["net"], r["leave"], r["present"]),
                         (Decimal("0.00"), Decimal("30000"), 1, 1))

    def test_deduction_never_exceeds_basic_salary(self):
        e = emp("A", "1000")
        for d in range(1, 32):
            mark(e, 2026, 1, d, "A")
        r = self.row(e, 1, 2026)
        self.assertEqual((r["deduction"], r["net"]), (Decimal("1000"), Decimal("0")))

    def test_only_selected_month_counts_and_inactive_employees_are_skipped(self):
        e = emp("A", "31000")
        mark(e, 2026, 2, 1, "A")
        inactive = emp("B", "1000", employment_status="INACTIVE")
        rows = svc.calculate_payroll_rows(1, 2026)
        self.assertEqual(next(r for r in rows if r["emp"].pk == e.pk)["deduction"], Decimal("0.00"))
        self.assertNotIn(inactive.pk, [r["emp"].pk for r in rows])


class PayrollGenerationTests(TestCase):
    def test_generate_creates_one_row_per_active_employee_and_is_repeatable(self):
        a = emp("A", "31000")
        emp("B", "31000")
        mark(a, 2026, 1, 1, "A")
        self.assertEqual(svc.generate_payroll(1, 2026), 2)
        self.assertEqual(svc.generate_payroll(1, 2026), 2)
        self.assertEqual(Salary.objects.filter(month=1, year=2026).count(), 2)
        s = Salary.objects.get(emp=a, month=1, year=2026)
        self.assertEqual((s.gross, s.deduction, s.net), (Decimal("31000"), Decimal("1000"), Decimal("30000")))

    def test_generation_is_all_or_nothing(self):
        emp("A", "31000"), emp("B", "31000")
        real = Salary.objects.update_or_create
        calls = {"n": 0}

        def flaky(*args, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return real(*args, **kw)

        with mock.patch.object(Salary.objects, "update_or_create", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                svc.generate_payroll(1, 2026)
        self.assertEqual(Salary.objects.count(), 0)

    def test_attendance_totals_for_a_slip(self):
        e = emp("A", "31000")
        mark(e, 2026, 1, 1, "P")
        mark(e, 2026, 1, 2, "H")
        mark(e, 2026, 2, 1, "A")  # other month, ignored
        svc.generate_payroll(1, 2026)
        slip = Salary.objects.get(emp=e, month=1, year=2026)
        self.assertEqual(svc.attendance_totals(slip), {"present": 1, "half": 1, "absent": 0, "leave": 0})
