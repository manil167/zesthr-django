from django.utils import timezone
from datetime import date
from django.urls import reverse

from core import roles
from core.models import Attendance, Employee, LeaveApplication

from .test_permissions import BaseTest, make_user

LEAVE = {"leave_type": "CL", "start_date": "2026-03-02", "end_date": "2026-03-02", "reason": "family"}


class MissingProfileTests(BaseTest):
    """Regression: leave used to be silently filed under the first active employee."""

    def test_user_without_profile_cannot_apply_for_leave(self):
        self.login(self.nobody)
        before = LeaveApplication.objects.count()
        self.assertEqual(self.client.get(reverse("leave_apply")).status_code, 403)
        self.assertEqual(self.client.post(reverse("leave_apply"), LEAVE).status_code, 403)
        self.assertEqual(LeaveApplication.objects.count(), before)

    def test_hr_staff_without_profile_cannot_apply_either(self):
        for user in (self.hr_admin, self.superuser):
            self.login(user)
            self.assertEqual(self.client.post(reverse("leave_apply"), LEAVE).status_code, 403)
        self.assertFalse(LeaveApplication.objects.filter(reason="family").exists())

    def test_explains_why_in_the_403_page(self):
        self.login(self.nobody)
        r = self.client.get(reverse("leave_apply"))
        self.assertContains(r, "No employee profile is linked", status_code=403)

    def test_user_without_profile_cannot_punch(self):
        self.login(self.nobody)
        self.assertEqual(self.client.post(reverse("attendance_today"), {"action": "check_in"}).status_code, 403)
        self.assertEqual(Attendance.objects.filter(date=timezone.localdate()).count(), 0)

    def test_nav_does_not_offer_leave_application_without_profile(self):
        self.login(self.hr_admin)
        self.assertNotContains(self.client.get(reverse("leaves_list")), reverse("leave_apply"))


class SubmittedIdsCannotOverrideIdentityTests(BaseTest):
    def test_leave_is_always_filed_for_the_logged_in_employee(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("leave_apply"), {**LEAVE, "emp": self.bob.pk, "emp_id": self.bob.pk,
                                                       "status": "APPROVED"})
        self.assertEqual(r.status_code, 302)
        leave = LeaveApplication.objects.get(reason="family")
        self.assertEqual(leave.emp, self.alice)
        self.assertEqual(leave.status, "PENDING")

    def test_punch_with_other_employee_id_is_rejected_not_redirected_to_self(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("attendance_today"), {"emp_id": self.bob.pk, "action": "check_in"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Attendance.objects.filter(date=timezone.localdate()).exists())

    def test_punch_with_own_id_works(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("attendance_today"), {"emp_id": self.alice.pk, "action": "check_in"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Attendance.objects.filter(emp=self.alice, date=timezone.localdate()).exists())

    def test_garbage_employee_id_is_a_404_not_a_server_error(self):
        for user in (self.alice_user, self.hr_admin):
            self.login(user)
            r = self.client.post(reverse("attendance_today"), {"emp_id": "abc", "action": "check_in"})
            self.assertEqual(r.status_code, 404)

    def test_attendance_manager_can_still_mark_others(self):
        self.login(self.hr_officer)
        r = self.client.post(reverse("attendance_today"), {"emp_id": self.bob.pk, "action": "A"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Attendance.objects.get(emp=self.bob, date=timezone.localdate()).status, "A")


class InactiveEmployeeTests(BaseTest):
    def setUp(self):
        Employee.objects.filter(pk=self.alice.pk).update(is_active=False, employment_status="INACTIVE")

    def test_cannot_apply_or_punch(self):
        self.login(self.alice_user)
        self.assertEqual(self.client.post(reverse("leave_apply"), LEAVE).status_code, 403)
        self.assertEqual(self.client.post(reverse("attendance_today"), {"action": "check_in"}).status_code, 403)

    def test_can_still_read_own_history_and_payslips(self):
        self.login(self.alice_user)
        self.assertEqual(self.client.get(reverse("slip", args=[self.alice_slip.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("leaves_list")).status_code, 200)


class CrossEmployeeAccessTests(BaseTest):
    def test_employee_cannot_read_anothers_slip_log_or_leaves(self):
        self.login(self.alice_user)
        self.assertEqual(self.client.get(reverse("slip", args=[self.bob_slip.pk])).status_code, 404)
        r = self.client.get(reverse("attendance_list"), {"month": 1, "year": 2026, "emp": self.bob.pk})
        self.assertTrue(all(a.emp_id == self.alice.pk for a in r.context["records"]))
        self.assertNotIn(self.leave, list(self.client.get(reverse("leaves_list")).context["leaves"]))


class ProfileReassignmentTests(BaseTest):
    def setUp(self):
        # A free login must first accept an organization invitation.
        from apps.organizations.models import OrganizationMembership
        OrganizationMembership.objects.get_or_create(user=self.nobody, organization=self.alice.organization)

    def edit_url(self):
        return reverse("employee_edit", args=[self.alice.pk])

    def payload(self, **extra):
        return {"name": "Alice", "designation": "Dev", "basic_salary": "30000", "joined_on": "2026-01-01",
                "employment_status": "ACTIVE", **extra}

    def test_only_link_permission_sees_the_account_field(self):
        self.login(self.hr_officer)
        self.assertNotIn("user", self.client.get(self.edit_url()).context["form"].fields)
        self.login(self.hr_admin)
        self.assertIn("user", self.client.get(self.edit_url()).context["form"].fields)

    def test_crafted_post_cannot_reassign_account(self):
        self.login(self.hr_officer)
        self.client.post(self.edit_url(), self.payload(user=self.nobody.pk))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.user, self.alice_user)

    def test_hr_admin_can_link_a_free_account(self):
        self.login(self.hr_admin)
        r = self.client.post(self.edit_url(), self.payload(user=self.nobody.pk))
        self.assertEqual(r.status_code, 302)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.user, self.nobody)

    def test_cannot_link_an_account_already_used_by_another_employee(self):
        self.login(self.hr_admin)
        r = self.client.post(self.edit_url(), self.payload(user=self.bob_user.pk))
        self.assertEqual(r.status_code, 200)  # form re-rendered with an error
        self.assertIn("user", r.context["form"].errors)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.user, self.alice_user)

    def test_account_choices_exclude_linked_and_inactive_users(self):
        dormant = make_user("dormant", is_active=False)
        self.login(self.hr_admin)
        choices = set(self.client.get(self.edit_url()).context["form"].fields["user"].queryset)
        self.assertIn(self.alice_user, choices)
        self.assertIn(self.nobody, choices)
        self.assertNotIn(self.bob_user, choices)
        self.assertNotIn(dormant, choices)

    def test_link_permission_is_hr_admin_only(self):
        for user, expected in ((self.hr_admin, True), (self.hr_officer, False), (self.payroll_officer, False),
                               (self.manager, False), (self.sysadmin, False)):
            self.assertEqual(user.has_perm(roles.LINK_EMPLOYEE_ACCOUNT), expected, user.username)


class AdminSiteIsNotABackDoorTests(BaseTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for u in (cls.hr_officer, cls.payroll_officer, cls.hr_admin):
            u.is_staff = True
            u.save()

    def fields(self, user):
        self.login(user)
        r = self.client.get(reverse("admin:core_employee_change", args=[self.alice.pk]))
        self.assertEqual(r.status_code, 200)
        form = r.context["adminform"]
        return set(form.form.fields), set(form.readonly_fields)

    def test_hr_officer_sees_no_salary_and_cannot_edit_account_link(self):
        editable, readonly = self.fields(self.hr_officer)
        self.assertNotIn("basic_salary", editable | readonly)
        self.assertIn("user", readonly)
        page = self.client.get(reverse("admin:core_employee_change", args=[self.alice.pk]))
        self.assertNotContains(page, "30000.00")

    def test_payroll_officer_can_view_but_not_edit_salary(self):
        editable, readonly = self.fields(self.payroll_officer)
        self.assertIn("basic_salary", readonly)
        self.assertNotIn("basic_salary", editable)
        self.assertContains(self.client.get(reverse("admin:core_employee_change", args=[self.alice.pk])), "30000.00")

    def test_hr_admin_can_edit_both(self):
        editable, readonly = self.fields(self.hr_admin)
        self.assertTrue({"basic_salary", "user"} <= editable)
        self.assertFalse({"basic_salary", "user"} & readonly)

    def test_hr_officer_cannot_add_employees_in_admin(self):
        self.login(self.hr_officer)
        self.assertEqual(self.client.get(reverse("admin:core_employee_add")).status_code, 403)
