from django.utils import timezone
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from core import roles
from core.models import Attendance, Employee, LeaveApplication, Salary
from core.roles import ROLE_PERMISSIONS, sync_roles
from apps.organizations.models import Organization, OrganizationMembership


def make_user(username, role=None, **extra):
    user = User.objects.create_user(username, password="pw-12345-x", **extra)
    if role:
        user.groups.add(Group.objects.get(name=role))
        # Privilege and explicit tenant membership are separate prerequisites.
        from core.models import default_organization
        OrganizationMembership.objects.get_or_create(user=user, organization=default_organization())
    return user


class BaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hr_admin = make_user("hradmin", roles.HR_ADMIN)
        cls.hr_officer = make_user("hrofficer", roles.HR_OFFICER)
        cls.payroll_officer = make_user("payroll", roles.PAYROLL_OFFICER)
        cls.sysadmin = make_user("sysadmin", roles.SYSTEM_ADMIN)
        cls.manager = make_user("manager", roles.DEPARTMENT_MANAGER)
        cls.plain_staff = make_user("plainstaff", is_staff=True)  # is_staff alone grants nothing
        cls.nobody = make_user("nobody")  # logged in, no role, no employee profile
        cls.superuser = User.objects.create_superuser("root", password="pw-12345-x")

        cls.alice_user = make_user("alice", roles.EMPLOYEE)
        cls.bob_user = make_user("bob", roles.EMPLOYEE)
        cls.alice = Employee.objects.create(user=cls.alice_user, name="Alice", designation="Dev", basic_salary=Decimal("30000"), joined_on=date(2025, 1, 1))
        cls.bob = Employee.objects.create(user=cls.bob_user, name="Bob", designation="QA", basic_salary=Decimal("31000"), joined_on=date(2025, 1, 1))
        cls.alice_slip = Salary.objects.create(emp=cls.alice, month=1, year=2026, gross=30000, net=30000)
        cls.bob_slip = Salary.objects.create(emp=cls.bob, month=1, year=2026, gross=31000, net=31000)
        cls.alice_att = Attendance.objects.create(emp=cls.alice, date=date(2026, 1, 5))
        cls.leave = LeaveApplication.objects.create(
            emp=cls.bob, leave_type="CL", start_date=date(2026, 2, 2), end_date=date(2026, 2, 2), reason="x")

    def login(self, user):
        self.client.force_login(user)
        return self.client


class RoleDefinitionTests(BaseTest):
    def test_all_roles_exist(self):
        for role in ROLE_PERMISSIONS:
            self.assertTrue(Group.objects.filter(name=role).exists(), role)

    def test_sync_is_idempotent_and_revokes_removed_permissions(self):
        group = Group.objects.get(name=roles.HR_OFFICER)
        extra = group.permissions.model.objects.get(codename="generate_payroll")
        group.permissions.add(extra)
        sync_roles()
        sync_roles()
        codes = {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()}
        self.assertEqual(codes, set(ROLE_PERMISSIONS[roles.HR_OFFICER]))

    def test_sysadmin_has_no_pay_access(self):
        for perm in (roles.VIEW_EMPLOYEE_PAY, roles.VIEW_PAYROLL_REPORT, roles.VIEW_ALL_PAYSLIPS):
            self.assertFalse(self.sysadmin.has_perm(perm))

    def test_finance_and_auditor_roles_are_limited_to_their_current_scope(self):
        finance = make_user("finance", roles.FINANCE_OFFICER)
        auditor = make_user("auditor", roles.AUDITOR)

        self.assertTrue(finance.has_perm(roles.VIEW_PAYROLL_REPORT))
        self.assertFalse(finance.has_perm(roles.GENERATE_PAYROLL))
        self.assertTrue(auditor.has_perm(roles.VIEW_SECURITY_LOG))
        self.assertFalse(auditor.has_perm(roles.VIEW_EMPLOYEE_PAY))

    def test_manager_has_no_organisation_wide_permission(self):
        self.assertEqual(ROLE_PERMISSIONS[roles.DEPARTMENT_MANAGER], [])
        self.assertFalse(self.manager.has_perm(roles.VIEW_EMPLOYEE))


class AnonymousAccessTests(BaseTest):
    def test_protected_urls_redirect_to_login(self):
        urls = [reverse(n) for n in ("dashboard", "employee_list", "employee_add", "attendance_today",
                                     "attendance_list", "attendance_add", "report", "payroll", "holidays",
                                     "leaves_list", "leave_apply")]
        urls += [reverse("slip", args=[self.alice_slip.pk]), reverse("employee_edit", args=[self.alice.pk]),
                 reverse("employee_delete", args=[self.alice.pk]), reverse("leave_action", args=[self.leave.pk])]
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login/", r["Location"], url)

    def test_home_page_hides_live_company_data_from_visitors(self):
        Attendance.objects.create(emp=self.alice, date=timezone.localdate())
        html = self.client.get(reverse("home")).content.decode()
        self.assertNotIn("Alice", html)

    def test_authorized_home_preserves_zero_counts_and_empty_feed(self):
        self.login(self.hr_admin)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["present_count"], 0)
        self.assertContains(response, "No attendance records today.")
        self.assertNotContains(response, "Asha Verma")
        self.assertRegex(response.content.decode(), r'Present Today</span>\s*<div class="mock-val">0</div>')

    def test_home_page_shows_live_data_to_authorized_staff(self):
        Attendance.objects.create(emp=self.alice, date=timezone.localdate())
        self.login(self.hr_admin)
        self.assertIn("Alice", self.client.get(reverse("home")).content.decode())


class EmployeeCannotUseAdminFunctionsTests(BaseTest):
    def test_direct_url_access_is_forbidden(self):
        self.login(self.alice_user)
        for url in (reverse("employee_list"), reverse("employee_add"),
                    reverse("employee_edit", args=[self.bob.pk]), reverse("employee_delete", args=[self.bob.pk]),
                    reverse("attendance_add"), reverse("attendance_edit", args=[self.alice_att.pk]),
                    reverse("attendance_delete", args=[self.alice_att.pk]),
                    reverse("report"), reverse("payroll")):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_cannot_delete_employee_or_attendance_by_post(self):
        self.login(self.alice_user)
        self.client.post(reverse("employee_delete", args=[self.bob.pk]))
        self.client.post(reverse("attendance_delete", args=[self.alice_att.pk]))
        self.assertTrue(Employee.objects.filter(pk=self.bob.pk).exists())
        self.assertTrue(Attendance.objects.filter(pk=self.alice_att.pk).exists())

    def test_cannot_generate_payroll(self):
        self.login(self.alice_user)
        before = Salary.objects.count()
        self.assertEqual(self.client.post(reverse("payroll")).status_code, 403)
        self.assertEqual(Salary.objects.count(), before)

    def test_cannot_approve_leave(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("leave_action", args=[self.leave.pk]), {"action": "approve"})
        self.assertEqual(r.status_code, 403)
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, "PENDING")

    def test_cannot_mark_another_employee(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("attendance_today"), {"emp_id": self.bob.pk, "action": "check_in"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Attendance.objects.filter(emp=self.bob, date=timezone.localdate()).exists())

    def test_cannot_grant_self_leave_status(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("attendance_today"), {"emp_id": self.alice.pk, "action": "L"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Attendance.objects.filter(emp=self.alice, date=timezone.localdate(), status="L").exists())

    def test_can_punch_own_attendance(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("attendance_today"), {"action": "check_in"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Attendance.objects.get(emp=self.alice, date=timezone.localdate()).check_in)

    def test_sees_only_own_attendance_log(self):
        Attendance.objects.create(emp=self.bob, date=date(2026, 1, 6))
        self.login(self.alice_user)
        r = self.client.get(reverse("attendance_list"), {"month": 1, "year": 2026, "emp": self.bob.pk})
        self.assertEqual({a.emp_id for a in r.context["records"]}, {self.alice.pk})

    def test_sees_only_own_leaves(self):
        self.login(self.alice_user)
        self.assertEqual(list(self.client.get(reverse("leaves_list")).context["leaves"]), [])


class PayslipOwnershipTests(BaseTest):
    def test_own_slip_ok_and_other_is_404(self):
        self.login(self.alice_user)
        self.assertEqual(self.client.get(reverse("slip", args=[self.alice_slip.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("slip", args=[self.bob_slip.pk])).status_code, 404)

    def test_user_without_profile_or_permission_is_forbidden(self):
        self.login(self.nobody)
        self.assertEqual(self.client.get(reverse("slip", args=[self.alice_slip.pk])).status_code, 403)

    def test_payroll_and_hr_admin_can_open_any_slip(self):
        for user in (self.payroll_officer, self.hr_admin):
            self.login(user)
            self.assertEqual(self.client.get(reverse("slip", args=[self.bob_slip.pk])).status_code, 200)

    def test_hr_officer_cannot_open_slips(self):
        self.login(self.hr_officer)
        self.assertEqual(self.client.get(reverse("slip", args=[self.bob_slip.pk])).status_code, 403)


class NoRoleUsersTests(BaseTest):
    def test_is_staff_alone_grants_nothing(self):
        self.login(self.plain_staff)
        for name in ("employee_list", "payroll", "report", "dashboard", "attendance_list", "leaves_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_no_profile_no_role_gets_no_dashboard(self):
        self.login(self.nobody)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("attendance_today")).status_code, 403)

    def test_manager_and_sysadmin_cannot_reach_hr_pages(self):
        for user in (self.manager, self.sysadmin):
            self.login(user)
            for name in ("employee_list", "payroll", "report"):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, (user, name))

    def test_inactive_account_is_refused(self):
        self.hr_admin.is_active = False
        self.hr_admin.save()
        self.client.force_login(self.hr_admin)
        self.assertEqual(self.client.get(reverse("employee_list")).status_code, 302)


class TenantIsolationTests(BaseTest):
    def test_hr_cannot_open_an_employee_profile_from_another_organization(self):
        default_org = self.alice.organization
        other_org = Organization.objects.create(name="Other Organization", code="OTHER")
        outsider = Employee.objects.create(
            name="Outsider", designation="Dev", basic_salary=Decimal("30000"), organization=other_org
        )
        OrganizationMembership.objects.get_or_create(user=self.hr_admin, organization=default_org)

        self.login(self.hr_admin)
        self.assertEqual(self.client.get(reverse("employee_detail", args=[outsider.pk])).status_code, 404)


class RoleCapabilityTests(BaseTest):
    URLS = {
        "employee_list": ("hr_admin", "hr_officer", "payroll_officer", "superuser"),
        "report": ("hr_admin", "payroll_officer", "superuser"),
        "payroll": ("hr_admin", "payroll_officer", "superuser"),
        "dashboard": ("hr_admin", "hr_officer", "payroll_officer", "superuser"),
        "leaves_list": ("hr_admin", "hr_officer", "superuser"),
        "attendance_list": ("hr_admin", "hr_officer", "payroll_officer", "superuser"),
        "attendance_today": ("hr_admin", "hr_officer", "superuser"),
        "employee_add": ("hr_admin", "superuser"),
        "attendance_add": ("hr_admin", "hr_officer", "superuser"),
    }

    def test_matrix(self):
        for name, allowed in self.URLS.items():
            for attr in ("hr_admin", "hr_officer", "payroll_officer", "superuser"):
                self.login(getattr(self, attr))
                code = self.client.get(reverse(name)).status_code
                expected = 200 if attr in allowed else 403
                self.assertEqual(code, expected, f"{attr} -> {name}")

    def test_hr_officer_cannot_generate_payroll_or_delete(self):
        self.login(self.hr_officer)
        self.assertEqual(self.client.post(reverse("payroll")).status_code, 403)
        self.assertEqual(self.client.get(reverse("employee_delete", args=[self.alice.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("attendance_delete", args=[self.alice_att.pk])).status_code, 403)

    def test_hr_officer_can_approve_leave(self):
        self.login(self.hr_officer)
        self.client.post(reverse("leave_action", args=[self.leave.pk]), {"action": "approve"})
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, "APPROVED")

    def test_payroll_officer_can_generate_but_not_edit_people_or_approve_leave(self):
        self.login(self.payroll_officer)
        self.assertEqual(self.client.post(reverse("payroll"), {}).status_code, 302)
        self.assertEqual(self.client.get(reverse("employee_edit", args=[self.alice.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("leave_action", args=[self.leave.pk]), {"action": "approve"}).status_code, 403)

    def test_salary_hidden_from_hr_officer_but_visible_to_payroll_and_hr_admin(self):
        self.login(self.hr_officer)
        self.assertNotContains(self.client.get(reverse("employee_list")), "30000.00")
        for user in (self.payroll_officer, self.hr_admin):
            self.login(user)
            self.assertContains(self.client.get(reverse("employee_list")), "30000.00")

    def test_hr_officer_cannot_change_salary_even_with_crafted_post(self):
        self.login(self.hr_officer)
        form = self.client.get(reverse("employee_edit", args=[self.alice.pk])).context["form"]
        self.assertNotIn("basic_salary", form.fields)
        self.client.post(reverse("employee_edit", args=[self.alice.pk]), {
            "name": "Alice", "designation": "Lead", "basic_salary": "999999", "joined_on": "2026-01-01", "employment_status": "ACTIVE"})
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.designation, "Lead")
        self.assertEqual(self.alice.basic_salary, Decimal("30000.00"))

    def test_dashboard_tiles_follow_permissions(self):
        self.login(self.hr_officer)
        ctx = self.client.get(reverse("dashboard")).context
        self.assertIsNone(ctx["payroll"])
        self.assertIsNotNone(ctx["pending_leaves"])
        self.login(self.payroll_officer)
        ctx = self.client.get(reverse("dashboard")).context
        self.assertIsNotNone(ctx["payroll"])
        self.assertIsNone(ctx["pending_leaves"])
