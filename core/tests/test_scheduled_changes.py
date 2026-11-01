from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from django.contrib.auth.models import User, Permission
from apps.organizations.models import Organization, OrganizationMembership
from core.models import Employee, Department, ScheduledEmploymentChange
from core.services.employees import apply_scheduled_change


class ScheduledChangeSafetyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="First", code="FIRST")
        self.other = Organization.objects.create(name="Other", code="OTHER")
        self.employee = Employee.objects.create(name="Employee", organization=self.org, basic_salary=1000)
        self.user = User.objects.create_user("hr")
        self.user.user_permissions.add(Permission.objects.get(codename="change_employee"))
        OrganizationMembership.objects.create(user=self.user, organization=self.org)
        self.change = ScheduledEmploymentChange.objects.create(
            employee=self.employee, effective_date=timezone.localdate(),
            designation="Lead", reason="Promotion")

    def test_future_date_rejected_by_service(self):
        self.change.effective_date += timedelta(days=1)
        self.change.save()
        with self.assertRaisesMessage(ValidationError, "not effective"):
            apply_scheduled_change(self.change)
        self.employee.refresh_from_db()
        self.assertNotEqual(self.employee.designation, "Lead")

    def test_cross_tenant_assignment_rejected(self):
        self.change.department = Department.objects.create(name="Other", code="OTHER", organization=self.other)
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.change.save()
        self.assertFalse(self.employee.history.exists())

    def test_blank_reason_rejected(self):
        self.change.reason = " "
        self.change.save()
        with self.assertRaises(ValidationError):
            apply_scheduled_change(self.change)

    def test_employee_of_other_tenant_is_hidden_and_cannot_be_changed(self):
        OrganizationMembership.objects.filter(user=self.user).update(organization=self.other)
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse("scheduled_employment_changes")), "Promotion")
        self.assertEqual(self.client.post(reverse("apply_scheduled_employment_change",
                                                args=[self.change.pk])).status_code, 404)
        with self.assertRaises(PermissionDenied):
            apply_scheduled_change(self.change, by=self.user)

    def test_valid_change_is_audited_and_cannot_be_reapplied(self):
        apply_scheduled_change(self.change, by=self.user)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.designation, "Lead")
        self.assertEqual(self.employee.history.get(field="designation").changed_by, self.user)
        with self.assertRaises(ValidationError):
            apply_scheduled_change(self.change, by=self.user)
