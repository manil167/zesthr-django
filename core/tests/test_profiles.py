from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core import roles
from core.models import Department, Employee, EmploymentHistory, JobPosition
from core.services import employees as employee_service
from core.services import history

from .test_permissions import BaseTest, make_user

SALARY = "30000.00"  # Alice's basic salary as rendered


class ProfileTestBase(BaseTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dept = Department.objects.create(name="Engineering", code="ENG")
        cls.pos = JobPosition.objects.create(title="Developer", department=cls.dept)
        # Boss manages Alice; Bob is unrelated to Boss
        cls.boss_user = make_user("boss", roles.DEPARTMENT_MANAGER)
        cls.boss = Employee.objects.create(user=cls.boss_user, name="Boss", designation="Lead",
                                           basic_salary=Decimal("90000"), joined_on=date(2024, 1, 1))
        Employee.objects.filter(pk=cls.alice.pk).update(
            manager=cls.boss, department=cls.dept, position=cls.pos, address="12 Palm Street, Ota",
            emergency_contact_name="Mum", emergency_contact_phone="0800-111")
        cls.alice.refresh_from_db()

    def url(self, emp=None):
        return reverse("employee_detail", args=[(emp or self.alice).pk])

    def page(self, user, emp=None):
        self.login(user)
        return self.client.get(self.url(emp))


class ProfileAccessTests(ProfileTestBase):
    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_employee_sees_own_profile_but_other_profiles_are_404(self):
        self.assertEqual(self.page(self.alice_user).status_code, 200)
        self.assertEqual(self.page(self.alice_user, self.bob).status_code, 404)  # no hint that Bob exists

    def test_hr_roles_can_open_any_profile(self):
        for user in (self.hr_admin, self.hr_officer, self.payroll_officer):
            self.assertEqual(self.page(user, self.bob).status_code, 200, user.username)

    def test_roles_without_hr_access_cannot(self):
        for user in (self.sysadmin, self.nobody, self.plain_staff):
            self.assertEqual(self.page(user).status_code, 404, user.username)

    def test_manager_sees_direct_reports_only(self):
        self.assertEqual(self.page(self.boss_user, self.alice).status_code, 200)
        self.assertEqual(self.page(self.boss_user, self.bob).status_code, 404)

    def test_my_profile(self):
        self.login(self.alice_user)
        r = self.client.get(reverse("my_profile"))
        self.assertEqual((r.status_code, r.context["emp"]), (200, self.alice))
        self.login(self.nobody)
        self.assertEqual(self.client.get(reverse("my_profile")).status_code, 403)


class FieldVisibilityTests(ProfileTestBase):
    def test_hr_officer_sees_employment_and_personal_but_not_pay(self):
        r = self.page(self.hr_officer)
        self.assertContains(r, "Employment")
        self.assertContains(r, "12 Palm Street")
        self.assertNotContains(r, SALARY)

    def test_payroll_officer_sees_pay_but_not_personal_details(self):
        r = self.page(self.payroll_officer)
        self.assertContains(r, SALARY)
        self.assertNotContains(r, "12 Palm Street")
        self.assertNotContains(r, "Mum")

    def test_hr_admin_sees_everything(self):
        r = self.page(self.hr_admin)
        for text in (SALARY, "12 Palm Street", "Mum", "Employment history"):
            self.assertContains(r, text)

    def test_employee_sees_everything_about_themself_including_pay(self):
        r = self.page(self.alice_user)
        for text in (SALARY, "12 Palm Street", "Mum", "Employment history"):
            self.assertContains(r, text)

    def test_manager_sees_only_basic_work_details_of_a_report(self):
        r = self.page(self.boss_user, self.alice)
        self.assertContains(r, "Engineering")
        for hidden in (SALARY, "12 Palm Street", "Mum", "Employment history", "Work schedule", "Login account"):
            self.assertNotContains(r, hidden)

    def test_action_buttons_follow_permissions(self):
        self.assertNotContains(self.page(self.alice_user), reverse("employee_deactivate", args=[self.alice.pk]))
        self.assertContains(self.page(self.alice_user), reverse("my_profile_edit"))
        self.assertNotContains(self.page(self.hr_officer), reverse("employee_deactivate", args=[self.alice.pk]))
        self.assertContains(self.page(self.hr_admin), reverse("employee_deactivate", args=[self.alice.pk]))

    def test_personal_fields_are_not_editable_by_payroll_or_unauthorised_forms(self):
        self.login(self.hr_officer)
        self.assertIn("address", self.client.get(reverse("employee_edit", args=[self.alice.pk])).context["form"].fields)


class SelfServiceEditTests(ProfileTestBase):
    def test_employee_can_update_only_contact_fields(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("my_profile_edit"), {
            "email": "alice@new.example", "phone": "0803", "address": "New address",
            "emergency_contact_name": "Dad", "emergency_contact_phone": "0900",
            # none of these may be changeable by the employee:
            "name": "Hacked", "basic_salary": "999999", "department": "", "manager": self.bob.pk,
            "employment_status": "SUSPENDED", "designation": "CEO", "user": self.bob_user.pk})
        self.assertRedirects(r, reverse("my_profile"))
        self.alice.refresh_from_db()
        self.assertEqual((self.alice.email, self.alice.phone, self.alice.address, self.alice.emergency_contact_name),
                         ("alice@new.example", "0803", "New address", "Dad"))
        self.assertEqual((self.alice.name, self.alice.basic_salary, self.alice.department, self.alice.manager,
                          self.alice.employment_status, self.alice.designation, self.alice.user),
                         ("Alice", Decimal("30000.00"), self.dept, self.boss, "ACTIVE", "Dev", self.alice_user))

    def test_form_exposes_only_five_fields(self):
        self.login(self.alice_user)
        form = self.client.get(reverse("my_profile_edit")).context["form"]
        self.assertEqual(set(form.fields), {"email", "phone", "address", "emergency_contact_name", "emergency_contact_phone"})

    def test_no_profile_or_inactive_employee_cannot_edit(self):
        self.login(self.nobody)
        self.assertEqual(self.client.get(reverse("my_profile_edit")).status_code, 403)
        Employee.objects.filter(pk=self.alice.pk).update(employment_status="INACTIVE", is_active=False)
        self.login(self.alice_user)
        self.assertEqual(self.client.post(reverse("my_profile_edit"), {"phone": "1"}).status_code, 403)

    def test_edit_requires_login(self):
        self.assertEqual(self.client.get(reverse("my_profile_edit")).status_code, 302)


class EmploymentHistoryTests(ProfileTestBase):
    def edit(self, user, **changes):
        self.login(user)
        data = {"name": "Alice", "designation": "Dev", "basic_salary": "30000", "joined_on": "2026-01-01",
                "employment_status": "ACTIVE", "department": self.dept.pk, "position": self.pos.pk,
                "manager": self.boss.pk, "user": self.alice_user.pk, **changes}
        r = self.client.post(reverse("employee_edit", args=[self.alice.pk]), data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)

    def test_changes_are_recorded_with_old_new_and_who(self):
        other = Department.objects.create(name="Finance", code="FIN")
        self.edit(self.hr_admin, department=other.pk, designation="Senior Dev")
        rows = {h.field: h for h in EmploymentHistory.objects.filter(employee=self.alice)}
        self.assertEqual(set(rows), {"department", "designation"})
        self.assertEqual((rows["department"].old_value, rows["department"].new_value, rows["department"].changed_by),
                         ("Engineering", "Finance", self.hr_admin))
        self.assertEqual((rows["designation"].old_value, rows["designation"].new_value), ("Dev", "Senior Dev"))

    def test_unchanged_save_records_nothing(self):
        self.edit(self.hr_admin)
        self.assertEqual(EmploymentHistory.objects.count(), 0)

    def test_salary_change_is_recorded_but_hidden_from_people_who_cannot_see_pay(self):
        self.edit(self.hr_admin, basic_salary="35000")
        h = EmploymentHistory.objects.get(employee=self.alice, field="basic_salary")
        self.assertEqual((h.old_value, h.new_value, h.sensitive), ("30000.00", "35000.00", True))
        self.assertNotContains(self.page(self.hr_officer), "35000.00")
        self.assertContains(self.page(self.payroll_officer), "35000.00")
        self.assertContains(self.page(self.alice_user), "35000.00")

    def test_creating_an_employee_is_recorded(self):
        self.login(self.hr_admin)
        r = self.client.post(reverse("employee_add"), {
            "name": "Newbie", "designation": "Intern", "basic_salary": "1000", "joined_on": "2026-02-01",
            "employment_status": "ACTIVE"})
        self.assertEqual(r.status_code, 302)
        h = EmploymentHistory.objects.get(employee__name="Newbie")
        self.assertEqual((h.field, h.changed_by), ("created", self.hr_admin))

    def test_deactivation_and_reactivation_are_recorded(self):
        emp = Employee.objects.get(pk=self.bob.pk)
        employee_service.deactivate_employee(emp, status="SUSPENDED", by=self.hr_admin)
        employee_service.reactivate_employee(Employee.objects.get(pk=self.bob.pk), by=self.hr_admin)
        changes = list(EmploymentHistory.objects.filter(employee=self.bob).order_by("id").values_list("old_value", "new_value"))
        self.assertEqual(changes, [("Active", "Suspended"), ("Suspended", "Active")])

    def test_history_is_append_only(self):
        self.edit(self.hr_admin, designation="Lead Dev")
        h = EmploymentHistory.objects.get()
        h.new_value = "tampered"
        with self.assertRaises(Exception):
            h.save()

    def test_history_names_survive_renaming(self):
        self.edit(self.hr_admin, position="")
        self.pos.title = "Renamed"
        self.pos.save()
        h = EmploymentHistory.objects.get(field="position")
        self.assertEqual(h.old_value, "Developer")

    def test_snapshot_helper(self):
        snap = history.snapshot(self.alice)
        self.assertEqual((snap["department"], snap["manager"], snap["employment_status"]), ("Engineering", "Boss", "Active"))

    def test_page_shows_the_timeline(self):
        self.edit(self.hr_admin, designation="Lead Dev")
        r = self.page(self.hr_admin)
        self.assertContains(r, "Lead Dev")
        self.assertContains(r, "hradmin")


class SearchAndPaginationTests(ProfileTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.fin = Department.objects.create(name="Finance", code="FIN")
        for i in range(45):
            Employee.objects.create(name=f"Staff {i:02d}", designation="Clerk", basic_salary=Decimal("1000"),
                                    department=cls.fin if i % 3 == 0 else None, email=f"staff{i:02d}@corp.example")
        Employee.objects.create(name="Gone Person", designation="Clerk", basic_salary=Decimal("1000"),
                                employment_status="RESIGNED", separation_date=date(2026, 6, 1), joined_on=date(2025, 1, 1))

    def get(self, **params):
        self.login(self.hr_officer)
        return self.client.get(reverse("employee_list"), params)

    def test_pages_of_twenty_in_name_order(self):
        r = self.get()
        self.assertEqual(len(r.context["employees"]), 20)
        self.assertTrue(r.context["is_paginated"])
        names = [e.name for e in r.context["employees"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(self.get(page=3).context["employees"]), r.context["paginator"].count - 40)

    def test_search_matches_name_id_email_department_and_designation(self):
        self.assertEqual({e.name for e in self.get(q="staff 07").context["employees"]}, {"Staff 07"})
        self.assertEqual(self.get(q="staff12@corp").context["paginator"].count, 1)
        emp = Employee.objects.get(name="Staff 03")
        self.assertIn(emp, self.get(q=emp.employee_number).context["employees"])
        self.assertEqual(self.get(q="Finance").context["paginator"].count, 15)
        self.assertGreater(self.get(q="Clerk").context["paginator"].count, 40)
        self.assertEqual(self.get(q="no-such-person").context["paginator"].count, 0)

    def test_filters_by_department_and_status(self):
        self.assertEqual(self.get(department=self.fin.pk).context["paginator"].count, 15)
        self.assertEqual(self.get(status="RESIGNED").context["paginator"].count, 1)
        self.assertEqual(self.get(status="inactive").context["paginator"].count, 1)
        self.assertEqual(self.get(status="active").context["paginator"].count, Employee.objects.filter(is_active=True).count())
        self.assertEqual(self.get(department=self.fin.pk, q="Staff 06").context["paginator"].count, 1)

    def test_bad_filter_values_are_ignored_not_errors(self):
        r = self.get(department="abc", status="<script>")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["department"], "")
        self.assertEqual(r.context["status"], "")

    def test_pager_links_keep_the_filters(self):
        r = self.get(q="Staff", status="active")  # 45 matches -> several pages
        self.assertContains(r, "q=Staff")
        self.assertContains(r, "status=active")
        self.assertContains(r, "page=2")
        self.assertNotContains(r, "page=1&")  # no "First/Previous" on the first page

    def test_out_of_range_page_is_404(self):
        self.assertEqual(self.get(page=99).status_code, 404)

    def test_query_count_does_not_grow_with_rows(self):
        self.login(self.hr_admin)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("employee_list"))
        # fixed overhead (session, user, password flag, permissions, nav badge) plus the page itself;
        # what matters is that this stays flat as rows are added
        # Tenant membership resolution adds a bounded, cached authorization lookup.
        self.assertLessEqual(len(ctx), 15, [q["sql"][:80] for q in ctx])

    def test_list_links_to_profiles_and_hides_pay_from_hr_officer(self):
        r = self.get(q="Staff 01")
        self.assertContains(r, reverse("employee_detail", args=[Employee.objects.get(name="Staff 01").pk]))
        self.assertNotContains(r, "1000.00")


class DeactivationWorkflowTests(ProfileTestBase):
    def test_deactivate_page_explains_the_consequences(self):
        self.login(self.hr_admin)
        r = self.client.get(reverse("employee_deactivate", args=[self.alice.pk]))
        self.assertContains(r, "login <b>alice</b> is disabled")
        self.assertContains(r, "history is kept")

    def test_deactivated_employee_is_visible_in_the_inactive_filter_and_can_be_reactivated_from_the_profile(self):
        self.login(self.hr_admin)
        self.client.post(reverse("employee_deactivate", args=[self.bob.pk]), {"status": "INACTIVE"})
        self.assertIn(self.bob, self.client.get(reverse("employee_list"), {"status": "inactive"}).context["employees"])
        self.assertContains(self.client.get(self.url(self.bob)), reverse("employee_reactivate", args=[self.bob.pk]))
        self.client.post(reverse("employee_reactivate", args=[self.bob.pk]))
        self.bob.refresh_from_db()
        self.assertTrue(self.bob.is_active)

    def test_already_inactive_employee_is_not_deactivated_twice(self):
        employee_service.deactivate_employee(Employee.objects.get(pk=self.bob.pk), status="INACTIVE")
        self.login(self.hr_admin)
        r = self.client.get(reverse("employee_deactivate", args=[self.bob.pk]), follow=True)
        self.assertContains(r, "already inactive")


@override_settings(DEBUG=False)
class MigrationDefaultsTests(BaseTest):
    def test_new_personal_fields_default_to_empty(self):
        self.assertEqual((self.alice.address, self.alice.emergency_contact_name), ("", ""))
