from datetime import date
from django.urls import reverse
from django.utils import timezone
from apps.organizations.models import Organization, OrganizationMembership
from core.models import Employee, Attendance, Salary, LeaveApplication
from .test_permissions import BaseTest


class TenantBoundaryTests(BaseTest):
    def setUp(self):
        self.other_org = Organization.objects.create(name="Foreign", code="FOREIGN")
        OrganizationMembership.objects.get_or_create(user=self.hr_admin, organization=self.alice.organization)
        self.foreign = Employee.objects.create(name="ForeignPerson", organization=self.other_org,
                                               basic_salary=5000, joined_on=date(2025, 1, 1))
        self.attendance = Attendance.objects.create(emp=self.foreign, date=timezone.localdate())
        self.salary = Salary.objects.create(emp=self.foreign, month=1, year=2026, gross=5000, net=5000)
        self.foreign_leave = LeaveApplication.objects.create(emp=self.foreign, start_date=date(2026, 3, 1),
                                                             end_date=date(2026, 3, 1), reason="Private reason")
        self.login(self.hr_admin)

    def test_administrative_lists_do_not_expose_other_organization(self):
        for name in ("employee_list", "attendance_list", "attendance_register",
                     "leaves_list", "attendance_history", "dashboard"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "ForeignPerson")
                self.assertNotContains(response, "Private reason")

    def test_other_organization_records_cannot_be_changed_by_id(self):
        for name, pk in (("employee_edit", self.foreign.pk), ("employee_delete", self.foreign.pk),
                         ("employee_deactivate", self.foreign.pk), ("employee_reactivate", self.foreign.pk),
                         ("attendance_edit", self.attendance.pk), ("slip", self.salary.pk),
                         ("leave_action", self.foreign_leave.pk)):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name, args=[pk]), {}).status_code, 404)

    def test_api_lists_and_details_are_tenant_scoped_and_paginated(self):
        for path, pk in (("employees", self.foreign.pk), ("attendance", self.attendance.pk),
                         ("payroll", self.salary.pk), ("leaves", self.foreign_leave.pk)):
            response = self.client.get(f"/api/v1/{path}/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("results", response.json())
            self.assertNotIn("ForeignPerson", response.content.decode())
            self.assertEqual(self.client.get(f"/api/v1/{path}/{pk}/").status_code, 404)

    def test_crafted_attendance_employee_is_rejected(self):
        response = self.client.post(reverse("attendance_today"),
                                    {"emp_id": self.foreign.pk, "action": "check_out"})
        self.assertEqual(response.status_code, 404)
