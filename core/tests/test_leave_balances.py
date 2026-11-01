from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from core.models import Employee, Holiday, LeaveEntitlement
from core.services import leave
from core.services.leave_balances import balance, adjust_balance


class LeaveBalanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("hr")
        self.employee = Employee.objects.create(name="Employee", basic_salary=1000, joined_on=date(2025, 1, 1))
        self.entitlement = LeaveEntitlement.objects.create(employee=self.employee, leave_type="CL",
                                                           year=2026, annual_days=24)

    def request(self, start, end=None):
        return leave.submit_leave(self.employee, leave_type="CL", start_date=start,
                                  end_date=end or start, reason="Family")

    def test_pending_and_approved_are_reconciled_without_double_counting(self):
        request = self.request(date(2026, 3, 2), date(2026, 3, 4))
        self.assertEqual(balance(self.entitlement).pending, 3)
        leave.approve_leave(request, by=self.user)
        result = balance(self.entitlement)
        self.assertEqual((result.approved, result.pending, result.remaining), (3, 0, 21))

    def test_monthly_accrual_credits_only_elapsed_months(self):
        self.entitlement.monthly_accrual = True
        self.entitlement.save()
        self.assertEqual(balance(self.entitlement, as_of=date(2026, 3, 1)).earned, Decimal("6.00"))
        self.assertEqual(balance(self.entitlement, as_of=date(2025, 12, 31)).earned, 0)

    def test_working_day_policy_excludes_weekends_and_holidays(self):
        self.entitlement.working_days_only = True
        self.entitlement.save()
        Holiday.objects.create(name="Holiday", date=date(2026, 3, 2))
        self.request(date(2026, 2, 27), date(2026, 3, 3))
        self.assertEqual(balance(self.entitlement).pending, 2)

    def test_overlapping_request_cannot_exceed_available_days(self):
        self.entitlement.annual_days = 1
        self.entitlement.save()
        self.request(date(2026, 3, 2))
        with self.assertRaisesMessage(ValidationError, "Insufficient"):
            self.request(date(2026, 3, 3))

    def test_year_boundary_checks_both_years(self):
        LeaveEntitlement.objects.create(employee=self.employee, leave_type="CL", year=2027, annual_days=0)
        with self.assertRaisesMessage(ValidationError, "2027"):
            self.request(date(2026, 12, 31), date(2027, 1, 2))
        self.assertFalse(self.employee.leaves.exists())

    def test_each_month_is_checked_for_a_cross_month_request(self):
        self.entitlement.monthly_limit = 1
        self.entitlement.save()
        with self.assertRaisesMessage(ValidationError, "March 2026"):
            self.request(date(2026, 2, 28), date(2026, 3, 2))

    def test_adjustment_is_explainable_and_immutable(self):
        adjustment = adjust_balance(self.entitlement, days=Decimal("-2"), reason="Reconciliation", by=self.user)
        self.assertEqual(balance(self.entitlement).remaining, 22)
        with self.assertRaises(Exception):
            adjustment.delete()
        with self.assertRaises(ValidationError):
            adjust_balance(self.entitlement, days=0, reason=" ", by=self.user)
