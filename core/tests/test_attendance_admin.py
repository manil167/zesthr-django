import threading
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from core import roles, selectors
from core.models import (Attendance, AttendanceAudit, AttendanceCorrectionRequest as Req, Department, Employee,
                         Holiday, WorkSchedule)
from core.services import attendance as att
from core.services import corrections
from core.services import exceptions as exc
from core.services.attendance import AttendanceError

from .test_permissions import BaseTest, make_user

TODAY = timezone.localdate()
D1 = TODAY - timedelta(days=3)
while D1.weekday() > 4:          # keep test days on working days
    D1 -= timedelta(days=1)


def code_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except AttendanceError as e:
        return e.code
    return None


class Base(BaseTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.eng = Department.objects.create(name="Engineering", code="ENG")
        cls.boss_user = make_user("boss", roles.DEPARTMENT_MANAGER)
        long_ago = date(2024, 1, 1)
        cls.boss = Employee.objects.create(user=cls.boss_user, name="Boss", designation="Lead",
                                           basic_salary=Decimal("1"), joined_on=long_ago)
        Employee.objects.filter(pk__in=[cls.alice.pk, cls.bob.pk]).update(joined_on=long_ago)
        Employee.objects.filter(pk=cls.alice.pk).update(manager=cls.boss, department=cls.eng)
        cls.alice.refresh_from_db()
        cls.bob.refresh_from_db()
        Attendance.objects.all().delete()


# ------------------------------------------------------------------ save_manual / bulk
class SaveManualTests(Base):
    def test_create_update_unchanged_with_audit(self):
        rec, outcome = att.save_manual(self.alice, D1, status="P", check_in=time(9), check_out=time(17), by=self.hr_admin, reason="forgot")
        self.assertEqual(outcome, "created")
        rec, outcome = att.save_manual(self.alice, D1, status="P", check_in=time(9), check_out=time(18), by=self.hr_admin, reason="late stay")
        self.assertEqual((outcome, rec.check_out), ("updated", time(18)))
        _, outcome = att.save_manual(self.alice, D1, status="P", check_in=time(9), check_out=time(18), by=self.hr_admin, reason="again")
        self.assertEqual(outcome, "unchanged")
        self.assertEqual(list(AttendanceAudit.objects.order_by("id").values_list("action", flat=True)), ["MANUAL_CREATE", "MANUAL_EDIT"])

    def test_reason_and_validity_are_enforced(self):
        self.assertEqual(code_of(att.save_manual, self.alice, D1, status="P", by=self.hr_admin, reason=" "), "reason_required")
        self.assertEqual(code_of(att.save_manual, self.alice, D1, status="P", check_out=time(17), by=self.hr_admin, reason="x"), "invalid")
        self.assertEqual(code_of(att.save_manual, self.alice, TODAY + timedelta(days=1), status="A", by=self.hr_admin, reason="x"), "invalid")
        self.assertFalse(Attendance.objects.exists())

    def test_absent_and_leave_drop_any_times_and_overwrite_false_protects_existing(self):
        rec, _ = att.save_manual(self.alice, D1, status="A", check_in=time(9), by=self.hr_admin, reason="x")
        self.assertEqual((rec.check_in, rec.status), (None, "A"))
        self.assertEqual(code_of(att.save_manual, self.alice, D1, status="L", by=self.hr_admin, reason="x", overwrite=False), "exists")
        self.assertEqual(Attendance.objects.get().status, "A")


class BulkMarkTests(Base):
    def run_bulk(self, people, **kw):
        kw.setdefault("status", "A")
        return att.bulk_mark(people, D1, by=self.hr_officer, reason="office closed", **kw)

    def test_marks_everyone_and_audits_each(self):
        res = self.run_bulk([self.alice, self.bob])
        self.assertEqual([r.outcome for r in res], ["created", "created"])
        self.assertEqual(AttendanceAudit.objects.filter(reason="office closed").count(), 2)

    def test_existing_records_are_skipped_unless_overwrite(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9))
        res = self.run_bulk([self.alice, self.bob])
        self.assertEqual({r.emp.name: r.outcome for r in res}, {"Alice": "skipped", "Bob": "created"})
        self.assertEqual(Attendance.objects.get(emp=self.alice).status, "P")
        res = self.run_bulk([self.alice], overwrite=True)
        self.assertEqual((res[0].outcome, Attendance.objects.get(emp=self.alice).status), ("updated", "A"))

    def test_one_bad_row_does_not_block_the_rest(self):
        Employee.objects.filter(pk=self.bob.pk).update(joined_on=TODAY)  # D1 is before Bob joined
        res = self.run_bulk([Employee.objects.get(pk=self.bob.pk), self.alice])
        self.assertEqual({r.emp.name: r.outcome for r in res}, {"Bob": "skipped", "Alice": "created"})
        self.assertIn("joined", [r for r in res if r.emp.name == "Bob"][0].message)

    def test_limits_and_reason(self):
        with self.assertRaises(AttendanceError):
            att.bulk_mark([self.alice], D1, status="A", by=self.hr_officer, reason="")
        with self.assertRaises(AttendanceError):
            att.bulk_mark([self.alice] * 501, D1, status="A", by=self.hr_officer, reason="x")


# ------------------------------------------------------------------ register + bulk views
class RegisterViewTests(Base):
    def get(self, user=None, **params):
        self.login(user or self.hr_officer)
        return self.client.get(reverse("attendance_register"), params)

    def test_permissions(self):
        self.assertEqual(self.get(self.alice_user).status_code, 403)
        self.assertEqual(self.get(self.payroll_officer).status_code, 200)  # may view, not edit
        self.client.logout()
        self.assertEqual(self.client.get(reverse("attendance_register")).status_code, 302)

    def test_counts_and_unmarked_follow_the_records(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9))
        Attendance.objects.create(emp=self.bob, date=D1, status="L")
        r = self.get(date=D1.isoformat())
        c = r.context["counts"]
        self.assertEqual((c["P"], c["L"]), (1, 1))
        self.assertEqual(c["unmarked"], Employee.objects.filter(is_active=True).count() - 2)
        self.assertEqual({row["emp"].name for row in self.get(date=D1.isoformat(), status="unmarked").context["rows"]},
                         {e.name for e in Employee.objects.filter(is_active=True)} - {"Alice", "Bob"})

    def test_rest_days_and_holidays_are_not_unmarked(self):
        sat = D1 + timedelta(days=(5 - D1.weekday()) % 7)
        self.assertEqual(self.get(date=sat.isoformat()).context["counts"]["unmarked"], 0)
        Holiday.objects.create(name="Founders Day", date=D1)
        r = self.get(date=D1.isoformat())
        self.assertEqual(r.context["counts"]["unmarked"], 0)
        self.assertContains(r, "Founders Day")

    def test_department_search_filters(self):
        names = {row["emp"].name for row in self.get(department=self.eng.pk).context["rows"]}
        self.assertEqual(names, {"Alice"})
        self.assertEqual({row["emp"].name for row in self.get(q="bob").context["rows"]}, {"Bob"})

    def test_inactive_employees_appear_only_where_relevant(self):
        Employee.objects.filter(pk=self.bob.pk).update(employment_status="INACTIVE", is_active=False)
        self.assertNotIn("Bob", {row["emp"].name for row in self.get(date=TODAY.isoformat()).context["rows"]})
        Attendance.objects.create(emp=self.bob, date=D1, status="P")
        self.assertIn("Bob", {row["emp"].name for row in self.get(date=D1.isoformat()).context["rows"]})

    def test_bulk_controls_only_for_those_allowed(self):
        self.assertContains(self.get(self.hr_officer), "Mark the ticked employees")
        self.assertNotContains(self.get(self.payroll_officer), "Mark the ticked employees")


class BulkViewTests(Base):
    def post(self, user, ids, **extra):
        self.login(user)
        data = {"date": D1.isoformat(), "status": "A", "reason": "office closed", "emp": ids, **extra}
        return self.client.post(reverse("attendance_bulk"), data, follow=True)

    def test_marks_selected_and_reports(self):
        r = self.post(self.hr_officer, [self.alice.pk, self.bob.pk])
        self.assertContains(r, "2 employee(s) updated")
        self.assertEqual(Attendance.objects.filter(date=D1, status="A").count(), 2)

    def test_skipped_rows_are_explained(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P")
        r = self.post(self.hr_officer, [self.alice.pk, self.bob.pk])
        self.assertContains(r, "1 skipped")
        self.assertContains(r, "already has a record")

    def test_validation_messages(self):
        self.assertContains(self.post(self.hr_officer, []), "Tick at least one employee")
        self.assertContains(self.post(self.hr_officer, [self.alice.pk], reason=""), "reason")
        self.assertFalse(Attendance.objects.exists())

    def test_permissions(self):
        self.assertEqual(self.client.post(reverse("attendance_bulk"), {}).status_code, 302)  # anonymous: to login
        for user in (self.alice_user, self.payroll_officer):
            self.login(user)
            self.assertEqual(self.client.post(reverse("attendance_bulk"), {"date": D1.isoformat(), "status": "A", "reason": "x", "emp": [self.alice.pk]}).status_code, 403)
        self.assertFalse(Attendance.objects.exists())

    def test_overwrite_needs_the_change_permission(self):
        extra = make_user("addonly")
        extra.user_permissions.add(*Permission.objects.filter(codename__in=["add_attendance", "mark_attendance", "view_all_attendance"]))
        self.login(extra)
        r = self.client.post(reverse("attendance_bulk"), {"date": D1.isoformat(), "status": "A", "reason": "x", "emp": [self.alice.pk], "overwrite": "on"})
        self.assertEqual(r.status_code, 403)

    def test_get_just_redirects(self):
        self.login(self.hr_officer)
        self.assertEqual(self.client.get(reverse("attendance_bulk")).status_code, 302)


# ------------------------------------------------------------------ filters on the log
class LogFilterTests(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for i in range(60):
            Attendance.objects.create(emp=cls.alice, date=TODAY - timedelta(days=i + 40), status="P")
        Attendance.objects.create(emp=cls.bob, date=D1, status="A")
        Attendance.objects.create(emp=cls.alice, date=D1, status="L")

    def get(self, **params):
        self.login(self.hr_admin)
        return self.client.get(reverse("attendance_list"), params)

    def test_date_range_overrides_month_and_is_inclusive(self):
        r = self.get(**{"from": (D1 - timedelta(days=1)).isoformat(), "to": D1.isoformat()})
        self.assertEqual({a.status for a in r.context["records"]}, {"A", "L"})
        self.assertContains(r, "replaces the month/year")

    def test_reversed_oversized_and_bad_ranges_are_handled(self):
        r = self.get(**{"from": D1.isoformat(), "to": (D1 - timedelta(days=5)).isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["date_from"], (D1 - timedelta(days=5)).isoformat())
        big = self.get(**{"from": "2000-01-01", "to": "2030-01-01"})
        self.assertEqual(big.context["date_to"], date(2000, 12, 31).isoformat())
        self.assertEqual(self.get(**{"from": "garbage"}).status_code, 200)

    def test_department_employee_status_filters(self):
        base = dict(**{"from": (D1 - timedelta(days=1)).isoformat(), "to": D1.isoformat()})
        self.assertEqual({a.emp.name for a in self.get(department=self.eng.pk, **base).context["records"]}, {"Alice"})
        self.assertEqual({a.emp.name for a in self.get(emp=self.bob.pk, **base).context["records"]}, {"Bob"})
        self.assertEqual({a.status for a in self.get(status="A", **base).context["records"]}, {"A"})

    def test_pagination_keeps_filters(self):
        wide = {"from": (TODAY - timedelta(days=120)).isoformat(), "to": TODAY.isoformat(), "emp": self.alice.pk}
        r = self.get(**wide)
        self.assertEqual(len(r.context["records"]), 50)
        self.assertContains(r, "page=2")
        self.assertContains(r, f"emp={self.alice.pk}")
        self.assertEqual(self.get(page=2, **wide).context["page_obj"].number, 2)

    def test_employees_only_ever_see_their_own_rows_whatever_the_filters(self):
        self.login(self.alice_user)
        r = self.client.get(reverse("attendance_list"), {"emp": self.bob.pk, "department": self.eng.pk, "status": "A",
                                                          "from": (D1 - timedelta(days=1)).isoformat(), "to": D1.isoformat()})
        self.assertEqual({a.emp_id for a in r.context["records"]}, set())  # Alice has no Absent row in range
        r = self.client.get(reverse("attendance_list"), {"emp": self.bob.pk, "from": (D1 - timedelta(days=1)).isoformat(), "to": D1.isoformat()})
        self.assertEqual({a.emp_id for a in r.context["records"]}, {self.alice.pk})

    def test_employee_sees_request_link_not_edit_links(self):
        self.login(self.alice_user)
        r = self.client.get(reverse("attendance_list"), {"from": (D1 - timedelta(days=1)).isoformat(), "to": D1.isoformat()})
        self.assertContains(r, "Request correction")
        self.assertNotContains(r, "/edit/")

    def test_manual_entry_form_is_prefilled_from_the_link(self):
        self.login(self.hr_officer)
        r = self.client.get(reverse("attendance_add"), {"emp": self.bob.pk, "date": D1.isoformat()})
        self.assertEqual((r.context["form"].initial["emp"], r.context["form"].initial["date"]), (self.bob.pk, D1))


# ------------------------------------------------------------------ correction requests
class SubmitTests(Base):
    def submit(self, emp=None, **kw):
        base = dict(date=D1, status="P", check_in=time(9), check_out=time(17), reason="forgot to punch", by=self.alice_user)
        base.update(kw)
        return corrections.submit(emp or self.alice, **base)

    def test_creates_a_pending_request_remembering_the_original(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9, 30))
        req = self.submit()
        self.assertEqual((req.status, req.requested_check_out, req.original_values["check_in"]), ("PENDING", time(17), "09:30:00"))

    def test_rules(self):
        self.assertEqual(code_of(self.submit, reason=" "), "reason_required")
        self.assertEqual(code_of(self.submit, date=TODAY + timedelta(days=1)), "future_date")
        self.assertEqual(code_of(self.submit, date=TODAY - timedelta(days=45)), "too_old")
        self.assertEqual(code_of(self.submit, check_out=time(8)), "invalid")
        Employee.objects.filter(pk=self.alice.pk).update(employment_status="INACTIVE", is_active=False)
        self.assertEqual(code_of(self.submit, emp=Employee.objects.get(pk=self.alice.pk)), "inactive_employee")

    def test_no_change_and_one_open_request_per_day(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9), check_out=time(17))
        self.assertEqual(code_of(self.submit), "no_change")
        self.submit(check_out=time(18))
        self.assertEqual(code_of(self.submit, check_out=time(19)), "already_pending")
        self.assertEqual(Req.objects.count(), 1)

    def test_window_is_configurable(self):
        with self.settings(CORRECTION_WINDOW_DAYS=60):
            self.submit(date=TODAY - timedelta(days=45))
        self.assertEqual(Req.objects.count(), 1)

    def test_cancel(self):
        req = self.submit()
        self.assertEqual(code_of(corrections.cancel, req, by=self.bob_user), "not_yours")
        corrections.cancel(req, by=self.alice_user)
        req.refresh_from_db()
        self.assertEqual(req.status, "CANCELLED")
        self.assertEqual(code_of(corrections.cancel, req, by=self.alice_user), "already_decided")
        self.submit()  # a cancelled request no longer blocks a new one


class DecideTests(Base):
    def setUp(self):
        self.req = corrections.submit(self.alice, date=D1, status="P", check_in=time(9), check_out=time(17),
                                      reason="forgot to punch", by=self.alice_user)

    def test_who_may_decide(self):
        self.assertTrue(corrections.can_review(self.hr_officer, self.req))
        self.assertTrue(corrections.can_review(self.hr_admin, self.req))
        self.assertTrue(corrections.can_review(self.boss_user, self.req))        # her manager
        for user in (self.bob_user, self.alice_user, self.payroll_officer, self.sysadmin, self.nobody):
            self.assertFalse(corrections.can_review(user, self.req), user.username)

    def test_nobody_decides_their_own_request_not_even_hr(self):
        hr_emp = Employee.objects.create(user=self.hr_officer, name="HR Person", designation="HR", basic_salary=Decimal("1"),
                                         joined_on=date(2024, 1, 1))
        own = corrections.submit(hr_emp, date=D1, status="A", reason="was off", by=self.hr_officer)
        self.assertFalse(corrections.can_review(self.hr_officer, own))
        self.assertTrue(corrections.can_review(self.hr_admin, own))
        self.assertEqual(code_of(corrections.decide, own, approve=True, by=self.hr_officer), "not_allowed")

    def test_approval_applies_the_change_and_links_the_audit(self):
        corrections.decide(self.req, approve=True, note="ok", by=self.boss_user)
        rec = Attendance.objects.get(emp=self.alice, date=D1)
        self.assertEqual((rec.status, rec.check_in, rec.check_out), ("P", time(9), time(17)))
        self.req.refresh_from_db()
        self.assertEqual((self.req.status, self.req.decided_by, self.req.decision_note), ("APPROVED", self.boss_user, "ok"))
        audit = AttendanceAudit.objects.get()
        self.assertEqual((audit.correction_request, audit.changed_by, audit.action), (self.req, self.boss_user, "MANUAL_CREATE"))
        self.assertIn(f"Correction request #{self.req.pk}", audit.reason)

    def test_approving_over_an_existing_record_records_before_and_after(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9, 45))
        req = Req.objects.get(pk=self.req.pk)
        req.status = "CANCELLED"
        req.save()
        new = corrections.submit(self.alice, date=D1, status="P", check_in=time(9), check_out=time(17), reason="x", by=self.alice_user)
        corrections.decide(new, approve=True, by=self.hr_admin)
        audit = AttendanceAudit.objects.get(action="MANUAL_EDIT")
        self.assertEqual((audit.old_values["check_in"], audit.new_values["check_in"]), ("09:45:00", "09:00:00"))

    def test_rejection_needs_a_note_and_changes_nothing(self):
        self.assertEqual(code_of(corrections.decide, self.req, approve=False, note=" ", by=self.hr_admin), "note_required")
        corrections.decide(self.req, approve=False, note="no evidence", by=self.hr_admin)
        self.req.refresh_from_db()
        self.assertEqual((self.req.status, self.req.decision_note), ("REJECTED", "no evidence"))
        self.assertFalse(Attendance.objects.exists())

    def test_a_decision_is_final(self):
        corrections.decide(self.req, approve=False, note="n", by=self.hr_admin)
        self.assertEqual(code_of(corrections.decide, self.req, approve=True, by=self.hr_admin), "already_decided")
        self.assertFalse(Attendance.objects.exists())

    def test_approval_that_can_no_longer_apply_rolls_back_cleanly(self):
        Employee.objects.filter(pk=self.alice.pk).update(joined_on=TODAY)  # the day is now before joining
        self.assertEqual(code_of(corrections.decide, self.req, approve=True, by=self.hr_admin), "invalid")
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "PENDING")
        self.assertFalse(Attendance.objects.exists())

    def test_reviewable_queryset_scopes_managers_to_their_team(self):
        corrections.submit(self.bob, date=D1, status="A", reason="x", by=self.bob_user)
        self.assertEqual({r.emp.name for r in corrections.reviewable_requests(self.boss_user)}, {"Alice"})
        self.assertEqual({r.emp.name for r in corrections.reviewable_requests(self.hr_officer)}, {"Alice", "Bob"})
        self.assertEqual(corrections.reviewable_requests(self.alice_user).count(), 0)


class CorrectionViewTests(Base):
    def payload(self, **kw):
        return {"date": D1.isoformat(), "status": "P", "check_in": "09:00", "check_out": "17:00", "reason": "forgot", **kw}

    def test_employee_submits_and_sees_it(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("correction_new"), self.payload(), follow=True)
        self.assertContains(r, "Request sent")
        self.assertContains(self.client.get(reverse("my_corrections")), "forgot")

    def test_form_errors_show_the_rule_message(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("correction_new"), self.payload(check_out="08:00"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Check-out must be after check-in")
        self.assertFalse(Req.objects.exists())

    def test_form_prefills_the_current_record(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9, 30))
        self.login(self.alice_user)
        r = self.client.get(reverse("correction_new"), {"date": D1.isoformat()})
        self.assertEqual(r.context["form"].initial["check_in"], time(9, 30))

    def test_needs_a_profile_and_login(self):
        self.assertEqual(self.client.get(reverse("correction_new")).status_code, 302)
        self.login(self.nobody)
        self.assertEqual(self.client.get(reverse("correction_new")).status_code, 403)

    def test_cancel_view_is_post_only_and_owner_only(self):
        req = corrections.submit(self.alice, date=D1, status="A", reason="x", by=self.alice_user)
        self.login(self.bob_user)
        self.assertEqual(self.client.post(reverse("correction_cancel", args=[req.pk])).status_code, 404)
        self.login(self.alice_user)
        self.client.get(reverse("correction_cancel", args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.status, "PENDING")
        self.client.post(reverse("correction_cancel", args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.status, "CANCELLED")

    def test_review_queue_visibility(self):
        corrections.submit(self.alice, date=D1, status="A", reason="alice's", by=self.alice_user)
        corrections.submit(self.bob, date=D1, status="A", reason="bob's", by=self.bob_user)
        self.login(self.boss_user)
        r = self.client.get(reverse("correction_list"))
        self.assertContains(r, "alice&#x27;s")
        self.assertNotContains(r, "bob&#x27;s")
        self.login(self.hr_officer)
        r = self.client.get(reverse("correction_list"))
        self.assertContains(r, "alice&#x27;s")
        self.assertContains(r, "bob&#x27;s")
        for user in (self.alice_user, self.payroll_officer, self.nobody):
            self.login(user)
            self.assertEqual(self.client.get(reverse("correction_list")).status_code, 403, user.username)

    def test_decide_view(self):
        req = corrections.submit(self.alice, date=D1, status="P", check_in=time(9), check_out=time(17), reason="x", by=self.alice_user)
        self.login(self.bob_user)
        self.assertEqual(self.client.post(reverse("correction_decide", args=[req.pk]), {"action": "approve"}).status_code, 404)
        self.login(self.boss_user)
        r = self.client.post(reverse("correction_decide", args=[req.pk]), {"action": "reject", "note": ""}, follow=True)
        self.assertContains(r, "Please say why")
        r = self.client.post(reverse("correction_decide", args=[req.pk]), {"action": "approve", "note": "fine"}, follow=True)
        self.assertContains(r, "attendance updated")
        self.assertEqual(Attendance.objects.get(emp=self.alice, date=D1).check_out, time(17))
        again = self.client.post(reverse("correction_decide", args=[req.pk]), {"action": "approve"}, follow=True)
        self.assertContains(again, "already approved")

    def test_decide_requires_post_and_ignores_external_next(self):
        req = corrections.submit(self.alice, date=D1, status="A", reason="x", by=self.alice_user)
        self.login(self.hr_admin)
        self.client.get(reverse("correction_decide", args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.status, "PENDING")
        r = self.client.post(reverse("correction_decide", args=[req.pk]), {"action": "approve", "next": "https://evil.example/"})
        self.assertEqual(r["Location"], reverse("correction_list"))

    def test_notifications_and_badges(self):
        req = corrections.submit(self.alice, date=D1, status="A", reason="x", by=self.alice_user)
        texts = [n["text"] for n in selectors.employee_notifications(self.boss, TODAY)]
        self.assertTrue(any("1 attendance correction request waiting" in t for t in texts))
        self.login(self.boss_user)
        r = self.client.get(reverse("dashboard"))
        self.assertTrue(r.context["pending_corrections"] == 1)
        corrections.decide(req, approve=True, note="ok", by=self.boss_user)
        texts = [n["text"] for n in selectors.employee_notifications(self.alice, TODAY)]
        self.assertTrue(any("correction request for" in t and "approved" in t for t in texts))

    def test_hr_dashboard_shows_pending_count_only_to_reviewers(self):
        corrections.submit(self.alice, date=D1, status="A", reason="x", by=self.alice_user)
        self.login(self.hr_admin)
        self.assertEqual(self.client.get(reverse("dashboard")).context["pending_corrections"], 1)
        self.login(self.payroll_officer)
        self.assertIsNone(self.client.get(reverse("dashboard")).context["pending_corrections"])

    def test_database_allows_only_one_pending_request_per_day(self):
        Req.objects.create(emp=self.alice, date=D1, reason="a")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Req.objects.create(emp=self.alice, date=D1, reason="b")
        Req.objects.create(emp=self.alice, date=D1, reason="c", status="REJECTED")


# ------------------------------------------------------------------ exceptions
class ExceptionFinderTests(Base):
    def setUp(self):
        self.sched = WorkSchedule.objects.create(name="Std", start_time=time(9), end_time=time(18), grace_minutes=10,
                                                 overtime_eligible=True, is_default=True)
        self.d0 = D1 - timedelta(days=4)
        while self.d0.weekday() > 4:
            self.d0 -= timedelta(days=1)

    def find(self, kinds, d_from=None, d_to=None, **kw):
        return exc.find_exceptions(d_from or self.d0, d_to or D1, kinds=kinds, emp_id=kw.pop("emp_id", self.alice.pk), **kw)

    def kinds_of(self, rows):
        return sorted({(r.kind, r.date) for r in rows})

    def test_missing_checkout_only_for_finished_days(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9))
        Attendance.objects.create(emp=self.alice, date=TODAY, status="P", check_in=time(9))
        rows = exc.find_exceptions(D1, TODAY, kinds=[exc.MISSING_CHECKOUT], emp_id=self.alice.pk)
        self.assertEqual([(r.date, r.kind) for r in rows], [(D1, "missing_checkout")])
        self.assertIn("no check-out", rows[0].detail)

    def test_present_without_times(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P")
        self.assertEqual(self.kinds_of(self.find([exc.NO_TIMES])), [("no_times", D1)])

    def test_unmarked_working_days_skip_weekends_holidays_and_non_employment(self):
        Holiday.objects.create(name="H", date=self.d0)
        rows = self.find([exc.UNMARKED], self.d0 - timedelta(days=7), D1)
        days = {r.date for r in rows}
        self.assertTrue(all(d.weekday() < 5 for d in days))
        self.assertNotIn(self.d0, days)
        Employee.objects.filter(pk=self.alice.pk).update(joined_on=D1)
        self.assertEqual({r.date for r in self.find([exc.UNMARKED], self.d0 - timedelta(days=7), D1)}, {D1})
        self.assertEqual(self.find([exc.UNMARKED], TODAY, TODAY), [])  # today is not over

    def test_unmarked_uses_the_employees_schedule_working_days(self):
        weekend = WorkSchedule.objects.create(name="Wknd", start_time=time(9), end_time=time(17), working_days=[5, 6])
        Employee.objects.filter(pk=self.alice.pk).update(work_schedule=weekend)
        rows = self.find([exc.UNMARKED], self.d0 - timedelta(days=7), D1)
        self.assertTrue(rows and all(r.date.weekday() in (5, 6) for r in rows))

    def test_late_early_overtime_use_the_schedule(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9, 25), check_out=time(17, 0))
        Attendance.objects.create(emp=self.alice, date=self.d0, status="P", check_in=time(9), check_out=time(19, 30))
        rows = self.find([exc.LATE, exc.EARLY, exc.OVERTIME])
        self.assertEqual(self.kinds_of(rows), sorted([("late", D1), ("early", D1), ("overtime", self.d0)]))
        self.assertIn("25 m late", [r for r in rows if r.kind == "late"][0].detail)

    def test_fixing_a_record_removes_it_from_the_list(self):
        rec = Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9))
        self.assertEqual(len(self.find([exc.MISSING_CHECKOUT])), 1)
        att.save_manual(self.alice, D1, status="P", check_in=time(9), check_out=time(17), by=self.hr_admin, reason="fixed")
        self.assertEqual(self.find([exc.MISSING_CHECKOUT]), [])
        rec.refresh_from_db()

    def test_department_filter_and_range_limit(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P")
        Attendance.objects.create(emp=self.bob, date=D1, status="P")
        rows = exc.find_exceptions(D1, D1, kinds=[exc.NO_TIMES], department_id=self.eng.pk)
        self.assertEqual({r.emp.name for r in rows}, {"Alice"})
        with self.assertRaises(ValueError):
            exc.find_exceptions(D1 - timedelta(days=100), D1, kinds=[exc.UNMARKED])

    def test_reversed_range_is_corrected(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P")
        self.assertEqual(len(exc.find_exceptions(D1, D1 - timedelta(days=2), kinds=[exc.NO_TIMES], emp_id=self.alice.pk)), 1)


class ExceptionViewTests(Base):
    def setUp(self):
        Attendance.objects.create(emp=self.alice, date=D1, status="P", check_in=time(9))

    def get(self, name="attendance_exceptions", user=None, **params):
        self.login(user or self.hr_officer)
        return self.client.get(reverse(name), {"from": (D1 - timedelta(days=2)).isoformat(), "to": D1.isoformat(), **params})

    def test_lists_problem_with_a_fix_link(self):
        r = self.get(kind="missing_checkout", emp=self.alice.pk)
        self.assertContains(r, "Missing check-out")
        rec = Attendance.objects.get(emp=self.alice, date=D1)
        self.assertContains(r, reverse("attendance_edit", args=[rec.pk]))

    def test_unmarked_row_links_to_a_prefilled_entry(self):
        r = self.get(kind="unmarked", emp=self.bob.pk)
        self.assertContains(r, f"emp={self.bob.pk}&date=")

    def test_missing_punches_preset_defaults(self):
        r = self.get("attendance_missing_punches")
        self.assertEqual(set(r.context["chosen"]), set(exc.MISSING_PUNCH_KINDS))
        self.assertContains(r, "Missing punches")

    def test_oversized_range_shows_a_message(self):
        r = self.get(**{"from": "2020-01-01"})
        self.assertContains(r, "at most 62 days")

    def test_permissions(self):
        for name in ("attendance_exceptions", "attendance_missing_punches"):
            self.assertEqual(self.get(name, user=self.alice_user).status_code, 403)
            self.assertEqual(self.get(name, user=self.boss_user).status_code, 403)
            self.assertEqual(self.get(name, user=self.payroll_officer).status_code, 200)

    def test_payroll_officer_sees_no_fix_links(self):
        r = self.get(user=self.payroll_officer, kind="missing_checkout", emp=self.alice.pk)
        self.assertNotContains(r, "/edit/")

    def test_invalid_filter_values_are_ignored(self):
        self.assertEqual(self.get(kind="<script>", department="abc", emp="x", page="zzz").status_code, 200)


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentDecisionTests(TransactionTestCase):
    """Several reviewers press Approve at the same moment (PostgreSQL only): it must apply exactly once."""

    serialized_rollback = True

    def test_simultaneous_approvals_apply_once(self):
        alice = Employee.objects.create(name="Alice", designation="x", basic_salary=Decimal("1"), joined_on=date(2024, 1, 1),
                                        user=make_user("alice_c"))
        reviewers = [make_user(f"hr{i}", roles.HR_OFFICER) for i in range(6)]
        for u in reviewers:
            u.groups.add(Group.objects.get(name=roles.HR_OFFICER))
        req = corrections.submit(alice, date=D1, status="P", check_in=time(9), check_out=time(17), reason="x", by=alice.user)
        results, barrier = [], threading.Barrier(len(reviewers))

        def worker(user):
            try:
                barrier.wait()
                corrections.decide(req, approve=True, by=user)
                results.append("ok")
            except AttendanceError as e:
                results.append(e.code)
            except Exception as e:  # pragma: no cover
                results.append(f"crash:{type(e).__name__}:{e}")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(u,)) for u in reviewers]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ["already_decided"] * 5 + ["ok"], results)
        self.assertEqual(AttendanceAudit.objects.filter(correction_request=req).count(), 1)
        self.assertEqual(Attendance.objects.filter(emp=alice, date=D1).count(), 1)
