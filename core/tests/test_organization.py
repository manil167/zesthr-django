from datetime import date, time
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.contrib import admin
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core import roles, selectors
from core.models import Attendance, Department, Employee, EmploymentType, JobPosition, ScheduledEmploymentChange, WorkSchedule, validate_working_days
from core.services import employees as svc
from apps.organizations.models import Organization

from .test_permissions import BaseTest, make_user


def emp(name, **kw):
    kw.setdefault("joined_on", date(2025, 1, 1))
    return Employee.objects.create(name=name, designation="x", basic_salary=Decimal("1000"), **kw)


class EmployeeIdentifierTests(TestCase):
    def test_numbers_are_generated_and_unique(self):
        a, b = emp("A"), emp("B")
        self.assertEqual(a.employee_number, f"EMP-{a.pk:05d}")
        self.assertNotEqual(a.employee_number, b.employee_number)
        a.refresh_from_db()
        self.assertEqual(a.employee_number, f"EMP-{a.pk:05d}")

    def test_manual_number_is_kept_and_duplicates_are_rejected(self):
        emp("A", employee_number="HR-001")
        with self.assertRaises(IntegrityError), transaction.atomic():
            emp("B", employee_number="HR-001")

    def test_blank_number_does_not_collide(self):
        self.assertEqual(emp("A", employee_number="").employee_number[:4], "EMP-")
        self.assertEqual(emp("B", employee_number="  ").employee_number[:4], "EMP-")

    def test_legacy_compatible_creates_use_the_default_organization(self):
        department = Department.objects.create(name="Operations", code="OPS")
        employee = emp("A", department=department)

        self.assertEqual(department.organization.code, "DEFAULT")
        self.assertEqual(employee.organization, department.organization)
        self.assertEqual(Organization.objects.filter(code="DEFAULT").count(), 1)


class StatusTests(TestCase):
    def test_is_active_follows_status(self):
        e = emp("A")
        self.assertTrue(e.is_active)
        for status, active in (("PROBATION", True), ("SUSPENDED", False), ("RESIGNED", False), ("ACTIVE", True)):
            e.employment_status = status
            e.separation_date = date(2026, 1, 1) if status == "RESIGNED" else None
            e.save()
            self.assertEqual(e.is_active, active, status)

    def test_separation_needs_a_date_not_before_joining(self):
        e = emp("A", joined_on=date(2026, 3, 1))
        e.employment_status = "RESIGNED"
        with self.assertRaises(ValidationError):
            e.full_clean()
        e.separation_date = date(2026, 2, 1)
        with self.assertRaises(ValidationError):
            e.full_clean()
        e.separation_date = date(2026, 4, 1)
        e.full_clean()


class ManagerTests(TestCase):
    def test_cannot_manage_yourself_or_form_a_loop(self):
        a, b, c = emp("A"), emp("B"), emp("C")
        b.manager, c.manager = a, b
        b.save(), c.save()
        a.manager = c  # a -> c -> b -> a
        with self.assertRaisesMessage(ValidationError, "loop"):
            a.full_clean()
        a.manager = a
        with self.assertRaisesMessage(ValidationError, "own manager"):
            a.full_clean()

    def test_direct_reports_and_selector(self):
        boss_user = make_user("boss")
        boss = emp("Boss", user=boss_user)
        r1, r2 = emp("R1", manager=boss), emp("R2", manager=boss)
        emp("Other")
        gone = emp("Gone", manager=boss, employment_status="INACTIVE")
        self.assertEqual(set(boss.direct_reports.all()), {r1, r2, gone})
        self.assertEqual(set(selectors.employees_managed_by(boss_user)), {r1, r2})  # active only
        self.assertEqual(selectors.employees_managed_by(make_user("nobody2")).count(), 0)

    def test_deleting_a_manager_keeps_the_team(self):
        boss = emp("Boss")
        r = emp("R", manager=boss)
        boss.delete()
        r.refresh_from_db()
        self.assertIsNone(r.manager)


class ReferenceDataTests(TestCase):
    def test_default_employment_types_exist(self):
        self.assertTrue({"Full-time", "Part-time", "Contract"} <= set(EmploymentType.objects.values_list("name", flat=True)))

    def test_department_and_position_cannot_be_deleted_while_in_use(self):
        d = Department.objects.create(name="Finance", code="FIN")
        p = JobPosition.objects.create(title="Accountant", department=d)
        emp("A", department=d, position=p)
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            d.delete()
        with self.assertRaises(ProtectedError):
            p.delete()

    def test_work_schedule_validation(self):
        for bad in ([], [7], [1, 1], ["Mon"], "weekdays", [True]):
            with self.assertRaises(ValidationError, msg=bad):
                validate_working_days(bad)
        validate_working_days([0, 1, 2, 3, 4])
        night = WorkSchedule(name="Night", start_time=time(22), end_time=time(6))
        night.full_clean()  # overnight is allowed
        with self.assertRaises(ValidationError):
            WorkSchedule(name="Bad", start_time=time(9), end_time=time(9)).full_clean()


class ScheduledEmploymentChangeTests(TestCase):
    def test_applies_once_and_records_history(self):
        employee = emp("A")
        employee.designation = "Developer"
        employee.save()
        change = ScheduledEmploymentChange.objects.create(
            employee=employee, effective_date=date(2026, 2, 1), reason="promotion", designation="Senior Developer"
        )

        svc.apply_scheduled_change(change)

        employee.refresh_from_db()
        change.refresh_from_db()
        self.assertEqual(employee.designation, "Senior Developer")
        self.assertIsNotNone(change.applied_at)
        self.assertTrue(employee.history.filter(field="designation", new_value="Senior Developer").exists())
        with self.assertRaises(ValidationError):
            svc.apply_scheduled_change(change)

    def test_command_applies_only_due_changes(self):
        due, future = emp("Due"), emp("Future")
        due_change = ScheduledEmploymentChange.objects.create(
            employee=due, effective_date=date.today(), reason="promotion", designation="Lead"
        )
        future_change = ScheduledEmploymentChange.objects.create(
            employee=future, effective_date=date(2099, 1, 1), reason="promotion", designation="Lead"
        )

        call_command("apply_scheduled_employment_changes")

        due_change.refresh_from_db()
        future_change.refresh_from_db()
        self.assertIsNotNone(due_change.applied_at)
        self.assertIsNone(future_change.applied_at)

    def test_applied_change_is_locked_in_admin(self):
        change = ScheduledEmploymentChange.objects.create(
            employee=emp("A"), effective_date=date.today(), reason="promotion", designation="Lead"
        )
        svc.apply_scheduled_change(change)
        change.refresh_from_db()
        model_admin = admin.site._registry[ScheduledEmploymentChange]
        request = type("Request", (), {"user": User.objects.create_superuser("admin")})()

        self.assertFalse(model_admin.has_change_permission(request, change))
        self.assertFalse(model_admin.has_delete_permission(request, change))


class SafeDeactivationServiceTests(TestCase):
    def test_deactivate_keeps_history_and_revokes_login(self):
        u = make_user("leaver")
        e = emp("Leaver", user=u)
        Attendance.objects.create(emp=e, date=date(2026, 1, 5))
        svc.deactivate_employee(e, status="RESIGNED", separation_date=date(2026, 2, 1), reason=" moved abroad ")
        e.refresh_from_db(), u.refresh_from_db()
        self.assertEqual((e.is_active, e.employment_status, e.separation_date, e.separation_reason),
                         (False, "RESIGNED", date(2026, 2, 1), "moved abroad"))
        self.assertFalse(u.is_active)
        self.assertEqual(e.attendance.count(), 1)

    def test_validation(self):
        e = emp("A")
        with self.assertRaises(ValidationError):
            svc.deactivate_employee(e, status="ACTIVE")
        with self.assertRaises(ValidationError):
            svc.deactivate_employee(e, status="TERMINATED")  # no date
        e.refresh_from_db()
        self.assertTrue(e.is_active)

    def test_suspension_needs_no_date_and_reactivation_restores_everything(self):
        u = make_user("sus")
        e = emp("Sus", user=u)
        svc.deactivate_employee(e, status="SUSPENDED", separation_date=date(2026, 1, 1))
        e.refresh_from_db()
        self.assertIsNone(e.separation_date)  # not a separation
        svc.reactivate_employee(e)
        e.refresh_from_db(), u.refresh_from_db()
        self.assertEqual((e.is_active, e.employment_status, u.is_active), (True, "ACTIVE", True))
        with self.assertRaises(ValidationError):
            svc.reactivate_employee(e, status="RESIGNED")

    def test_deactivation_is_all_or_nothing(self):
        from unittest import mock
        u = make_user("atomic")
        e = emp("Atomic", user=u)
        with mock.patch.object(User, "save", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                svc.deactivate_employee(e, status="INACTIVE")
        e.refresh_from_db()
        self.assertTrue(e.is_active)


class LifecycleViewTests(BaseTest):
    def test_only_hr_admin_can_deactivate(self):
        url = reverse("employee_deactivate", args=[self.bob.pk])
        for user in (self.hr_officer, self.payroll_officer, self.alice_user):
            self.login(user)
            self.assertEqual(self.client.get(url).status_code, 403, user.username)
            self.assertEqual(self.client.post(url, {"status": "INACTIVE"}).status_code, 403)
        self.bob.refresh_from_db()
        self.assertTrue(self.bob.is_active)

    def test_hr_admin_deactivates_then_reactivates(self):
        self.login(self.hr_admin)
        r = self.client.post(reverse("employee_deactivate", args=[self.bob.pk]),
                             {"status": "RESIGNED", "separation_date": date.today().isoformat(), "reason": "left"})
        self.assertEqual(r.status_code, 302)
        self.bob.refresh_from_db(), self.bob_user.refresh_from_db()
        self.assertEqual((self.bob.employment_status, self.bob_user.is_active), ("RESIGNED", False))
        self.client.post(reverse("employee_reactivate", args=[self.bob.pk]))
        self.bob.refresh_from_db(), self.bob_user.refresh_from_db()
        self.assertEqual((self.bob.employment_status, self.bob_user.is_active), ("ACTIVE", True))

    def test_form_errors_are_shown_not_crashed(self):
        self.login(self.hr_admin)
        r = self.client.post(reverse("employee_deactivate", args=[self.bob.pk]), {"status": "TERMINATED"})
        self.assertEqual(r.status_code, 200)
        self.bob.refresh_from_db()
        self.assertTrue(self.bob.is_active)

    def test_reactivate_requires_post(self):
        self.login(self.hr_admin)
        Employee.objects.filter(pk=self.bob.pk).update(employment_status="INACTIVE", is_active=False)
        self.client.get(reverse("employee_reactivate", args=[self.bob.pk]))
        self.bob.refresh_from_db()
        self.assertFalse(self.bob.is_active)

    def test_deactivated_employee_cannot_log_in_or_use_existing_session(self):
        self.login(self.alice_user)
        svc.deactivate_employee(self.alice, status="INACTIVE")
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)  # sent to login

    def test_deleting_an_employee_with_history_is_blocked(self):
        self.login(self.hr_admin)
        r = self.client.post(reverse("employee_delete", args=[self.alice.pk]), follow=True)
        self.assertTrue(Employee.objects.filter(pk=self.alice.pk).exists())
        self.assertContains(r, "Deactivate the employee instead")
        self.assertTrue(Attendance.objects.filter(emp=self.alice).exists())

    def test_deleting_an_employee_without_history_still_works(self):
        fresh = emp("Fresh")
        self.login(self.hr_admin)
        self.client.post(reverse("employee_delete", args=[fresh.pk]))
        self.assertFalse(Employee.objects.filter(pk=fresh.pk).exists())

    def test_employee_form_saves_organization_fields(self):
        d = Department.objects.create(name="Ops", code="OPS")
        p = JobPosition.objects.create(title="Analyst")
        self.login(self.hr_officer)  # may edit org data but not pay/accounts
        r = self.client.post(reverse("employee_edit", args=[self.alice.pk]), {
            "name": "Alice", "designation": "Dev", "joined_on": "2026-01-01", "employment_status": "PROBATION",
            "department": d.pk, "position": p.pk, "manager": self.bob.pk, "confirmation_date": "2026-07-01"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.alice.refresh_from_db()
        self.assertEqual((self.alice.department, self.alice.position, self.alice.manager, self.alice.employment_status),
                         (d, p, self.bob, "PROBATION"))

    def test_form_cannot_set_separation_status_or_manager_loop(self):
        self.login(self.hr_admin)
        url = reverse("employee_edit", args=[self.alice.pk])
        base = {"name": "Alice", "designation": "Dev", "basic_salary": "30000", "joined_on": "2026-01-01"}
        r = self.client.post(url, {**base, "employment_status": "RESIGNED"})
        self.assertEqual(r.status_code, 200)  # not an allowed choice here
        Employee.objects.filter(pk=self.bob.pk).update(manager=self.alice)
        r = self.client.post(url, {**base, "employment_status": "ACTIVE", "manager": self.bob.pk})
        self.assertIn("manager", r.context["form"].errors)

    def test_hr_admin_gets_reference_data_perms_officer_does_not(self):
        self.assertTrue(self.hr_admin.has_perm("core.add_department"))
        self.assertTrue(self.hr_admin.has_perm(roles.DEACTIVATE_EMPLOYEE))
        self.assertFalse(self.hr_officer.has_perm("core.add_department"))
        self.assertFalse(self.hr_officer.has_perm(roles.DEACTIVATE_EMPLOYEE))
