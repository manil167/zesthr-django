from datetime import date
from decimal import Decimal
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from core.models import Employee, Attendance, Salary, PayrollComponent, EmployeePayrollComponent, PayrollAdjustment
from core.services import payroll as service


class PayrollLifecycleTests(TestCase):
    def setUp(self):
        self.maker = User.objects.create_superuser("maker")
        self.reviewer = User.objects.create_superuser("reviewer")
        self.employee = Employee.objects.create(name="Employee", basic_salary=3100, joined_on=date(2025, 1, 1))
        self.run = service.create_payroll_run(1, 2026, self.maker, organization=self.employee.organization)

    def finalized(self):
        service.submit_payroll_for_approval(self.run, self.maker)
        service.approve_payroll(self.run, self.reviewer)
        return service.finalize_payroll(self.run, self.maker)

    def test_approval_requires_different_reviewer(self):
        service.submit_payroll_for_approval(self.run, self.maker)
        with self.assertRaisesMessage(ValidationError, "different"):
            service.approve_payroll(self.run, self.maker)

    def test_finalization_requires_approval(self):
        with self.assertRaises(ValidationError):
            service.finalize_payroll(self.run, self.maker)

    def test_finalization_uses_reviewed_snapshot_after_source_changes(self):
        service.submit_payroll_for_approval(self.run, self.maker)
        self.employee.basic_salary = 9000
        self.employee.name = "Renamed"
        self.employee.save()
        Attendance.objects.create(emp=self.employee, date=date(2026, 1, 2), status="A")
        service.approve_payroll(self.run, self.reviewer)
        service.finalize_payroll(self.run, self.maker)
        salary = Salary.objects.get()
        self.assertEqual(salary.net, Decimal("3100.00"))
        self.assertEqual(salary.calculation_snapshot["name"], "Employee")
        self.assertEqual(service.attendance_totals(salary)["absent"], 0)

    def test_finalized_payslips_cannot_be_recalculated_or_saved(self):
        self.finalized()
        with self.assertRaises(ValidationError):
            service.generate_payroll(1, 2026)
        salary = Salary.objects.get()
        salary.net = 1
        with self.assertRaises(ValidationError):
            salary.save()

    def test_reversal_retains_records_and_immutable_audit(self):
        run = self.finalized()
        service.reverse_payroll(run, user=self.maker, reason="Payment cancelled")
        self.assertEqual(Salary.objects.count(), 1)
        self.assertEqual(run.events.count(), 5)
        with self.assertRaises(Exception):
            run.events.all().delete()

    def test_components_and_only_approved_adjustments_are_included(self):
        component = PayrollComponent.objects.create(name="Allowance", code="ALLOW", component_type="EARNING",
                                                    organization=self.employee.organization)
        EmployeePayrollComponent.objects.create(employee=self.employee, component=component, amount=310,
                                                 effective_from=date(2026, 1, 16))
        PayrollAdjustment.objects.create(employee=self.employee, month=1, year=2026,
                                         component_type="DEDUCTION", description="Loan", amount=50, approved=True)
        PayrollAdjustment.objects.create(employee=self.employee, month=1, year=2026,
                                         component_type="EARNING", description="Unapproved", amount=999, approved=False)
        row = service.calculate_employee_payroll(self.employee, 1, 2026)
        self.assertEqual(row.additional_earnings, Decimal("160.00"))
        self.assertEqual(row.net_pay, Decimal("3210.00"))

    def test_overlapping_components_are_rejected(self):
        component = PayrollComponent.objects.create(name="Allowance", code="ALLOW", component_type="EARNING",
                                                    organization=self.employee.organization)
        for start in (date(2026, 1, 1), date(2026, 1, 2)):
            EmployeePayrollComponent.objects.create(employee=self.employee, component=component, amount=100,
                                                     effective_from=start)
        with self.assertRaisesMessage(ValidationError, "Overlapping"):
            service.calculate_employee_payroll(self.employee, 1, 2026)

    def test_calculation_queries_do_not_grow_per_employee(self):
        for index in range(25):
            Employee.objects.create(name=f"Person {index}", basic_salary=1000,
                                    organization=self.employee.organization, joined_on=date(2025, 1, 1))
        with CaptureQueriesContext(connection) as queries:
            rows = service.calculate_payroll_rows(1, 2026)
        self.assertEqual(len(rows), 26)
        self.assertLessEqual(len(queries), 8)

    def test_run_detail_supports_the_complete_http_workflow(self):
        self.client.force_login(self.maker)
        url = reverse("payroll_run_detail", args=[self.run.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"action": "submit"})
        self.client.force_login(self.reviewer)
        self.client.post(url, {"action": "approve"})
        self.client.force_login(self.maker)
        self.client.post(url, {"action": "finalize"})
        self.assertEqual(Salary.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("slip", args=[Salary.objects.get().pk])).status_code, 200)
