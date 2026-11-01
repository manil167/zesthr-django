from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import selectors
from core.models import Attendance, Employee, Holiday, LeaveApplication, Salary

from .test_permissions import BaseTest

TODAY = date(2026, 3, 18)  # a Wednesday


def mk(name="Dana", **kw):
    kw.setdefault("joined_on", date(2025, 1, 1))
    return Employee.objects.create(name=name, designation="Dev", basic_salary=Decimal("30000"), **kw)


def att(emp, d, status="P", cin=None, cout=None):
    return Attendance.objects.create(emp=emp, date=d, status=status, check_in=cin, check_out=cout)


def leave(emp, start, end, status="APPROVED", ltype="CL", note=""):
    return LeaveApplication.objects.create(emp=emp, leave_type=ltype, start_date=start, end_date=end,
                                           reason="r", status=status, admin_note=note)


class PunchStateTests(TestCase):
    def test_states(self):
        e = mk()
        self.assertEqual(selectors.punch_state(e, None), "not_in")
        self.assertEqual(selectors.punch_state(e, att(e, TODAY, cin=time(9))), "in")
        Attendance.objects.filter(emp=e).update(check_out=time(17))
        self.assertEqual(selectors.punch_state(e, Attendance.objects.get(emp=e)), "done")
        for status in ("L", "A"):
            self.assertEqual(selectors.punch_state(e, Attendance(emp=e, date=TODAY, status=status)), "marked")
        Employee.objects.filter(pk=e.pk).update(employment_status="INACTIVE", is_active=False)
        self.assertEqual(selectors.punch_state(Employee.objects.get(pk=e.pk), None), "inactive")


class MonthSummaryTests(TestCase):
    def test_counts_and_hours_match_the_records_for_this_month_only(self):
        e, other = mk(), mk("Other")
        att(e, date(2026, 3, 2), "P", time(9), time(17))      # 8h
        att(e, date(2026, 3, 3), "P", time(9), time(13, 30))  # 4.5h
        att(e, date(2026, 3, 4), "H")
        att(e, date(2026, 3, 5), "A")
        att(e, date(2026, 3, 6), "L")
        att(e, date(2026, 2, 27), "P", time(9), time(18))     # other month
        att(other, date(2026, 3, 2), "A")                      # other employee
        s = selectors.month_summary(e, TODAY)
        self.assertEqual((s["present"], s["half"], s["absent"], s["leave"], s["hours"], s["marked_days"]),
                         (2, 1, 1, 1, 12.5, 5))

    def test_empty_month(self):
        self.assertEqual(selectors.month_summary(mk(), TODAY)["marked_days"], 0)


class LeaveTakenTests(TestCase):
    def test_only_approved_days_inside_the_year_are_counted(self):
        e = mk()
        leave(e, date(2026, 3, 2), date(2026, 3, 4))                      # CL 3
        leave(e, date(2026, 5, 1), date(2026, 5, 1))                      # CL 1
        leave(e, date(2026, 6, 1), date(2026, 6, 1), ltype="SL")           # SL 1
        leave(e, date(2026, 7, 1), date(2026, 7, 5), status="REJECTED")    # ignored
        leave(e, date(2026, 8, 1), date(2026, 8, 5), status="PENDING")     # ignored
        leave(e, date(2025, 12, 30), date(2026, 1, 2))                     # crosses new year: 2 days count
        leave(mk("Other"), date(2026, 3, 2), date(2026, 3, 9))             # someone else's
        taken = dict(selectors.leave_taken_this_year(e, TODAY))
        self.assertEqual(taken["Casual Leave"], 6)
        self.assertEqual(taken["Sick Leave"], 1)

    def test_nothing_taken(self):
        self.assertEqual(selectors.leave_taken_this_year(mk(), TODAY), [])


class NotificationTests(TestCase):
    def texts(self, e, today=TODAY):
        return [n["text"] for n in selectors.employee_notifications(e, today)]

    def test_missed_punch_out_in_the_last_week_only(self):
        e = mk(phone="1", emergency_contact_name="x")
        att(e, TODAY - timedelta(days=2), "P", time(9))               # no check-out -> warn
        att(e, TODAY - timedelta(days=3), "P", time(9), time(17))     # fine
        att(e, TODAY - timedelta(days=20), "P", time(9))              # too old
        att(e, TODAY, "P", time(9))                                   # today: still working
        notes = self.texts(e)
        self.assertEqual(len([t for t in notes if "did not punch out" in t]), 1)
        self.assertIn("Mon 16 Mar", " ".join(notes))

    def test_pending_and_decided_leaves(self):
        e = mk(phone="1", emergency_contact_name="x")
        leave(e, date(2026, 4, 1), date(2026, 4, 1), status="PENDING")
        approved = leave(e, date(2026, 3, 30), date(2026, 3, 30), status="APPROVED", note="Enjoy")
        rejected = leave(e, date(2026, 3, 31), date(2026, 3, 31), status="REJECTED")
        LeaveApplication.objects.filter(pk__in=[approved.pk, rejected.pk]).update(
            applied_on=timezone.make_aware(datetime(2026, 3, 10, 9, 0)))
        old = leave(e, date(2025, 1, 1), date(2025, 1, 1), status="APPROVED")
        LeaveApplication.objects.filter(pk=old.pk).update(applied_on=timezone.make_aware(datetime(2025, 1, 1)))
        notes = self.texts(e)
        self.assertIn("1 leave request awaiting a decision.", notes)
        self.assertTrue(any("was approved. Note: Enjoy" in t for t in notes))
        self.assertTrue(any("not approved" in t for t in notes))
        self.assertFalse(any("01 Jan" in t for t in notes))  # decided long ago: not news

    def test_new_payslip_holiday_probation_and_incomplete_profile(self):
        e = mk(employment_status="PROBATION", confirmation_date=TODAY + timedelta(days=10))
        slip = Salary.objects.create(emp=e, month=2, year=2026, gross=1, net=1)
        Salary.objects.filter(pk=slip.pk).update(generated_on=timezone.make_aware(datetime(2026, 3, 10)))
        Holiday.objects.create(name="Founders Day", date=TODAY + timedelta(days=1))
        notes = self.texts(e)
        self.assertIn("Your payslip for February 2026 is available.", notes)
        self.assertIn("Tomorrow is a holiday: Founders Day.", notes)
        self.assertTrue(any("probation is due to end" in t for t in notes))
        self.assertTrue(any("Complete your contact details" in t for t in notes))

    def test_inactive_record_and_ordering(self):
        e = mk(phone="1", emergency_contact_name="x")
        att(e, TODAY - timedelta(days=1), "P", time(9))
        leave(e, date(2026, 4, 1), date(2026, 4, 1), status="PENDING")
        Employee.objects.filter(pk=e.pk).update(employment_status="INACTIVE", is_active=False)
        notes = selectors.employee_notifications(Employee.objects.get(pk=e.pk), TODAY)
        self.assertEqual([n["level"] for n in notes], sorted([n["level"] for n in notes], key={"warn": 0, "success": 1, "info": 2}.get))
        self.assertIn("inactive", notes[0]["text"])

    def test_quiet_when_there_is_nothing_to_say(self):
        self.assertEqual(self.texts(mk(phone="1", emergency_contact_name="x")), [])

    def test_never_more_than_the_limit(self):
        e = mk()
        for i in range(1, 7):
            att(e, TODAY - timedelta(days=i), "P", time(9))
        self.assertLessEqual(len(selectors.employee_notifications(e, TODAY)), 6)


class SnapshotScopeTests(BaseTest):
    def test_snapshot_contains_only_this_employees_rows(self):
        snap = selectors.employee_snapshot(self.alice, timezone.localdate())
        self.assertEqual({s.emp_id for s in snap["payslips"]}, {self.alice.pk})
        self.assertEqual({a.emp_id for a in snap["recent"]}, {self.alice.pk})
        self.assertEqual({l.emp_id for l in snap["leaves"]}, set())  # the only seeded leave is Bob's

    def test_payslips_newest_first_and_capped_at_six(self):
        for m in range(2, 9):
            Salary.objects.create(emp=self.alice, month=m, year=2026, gross=1, net=1)
        slips = list(selectors.employee_snapshot(self.alice, timezone.localdate())["payslips"])
        self.assertEqual(len(slips), 6)
        self.assertEqual([(s.year, s.month) for s in slips], sorted([(s.year, s.month) for s in slips], reverse=True))

    def test_holidays_are_the_next_three_with_days_away(self):
        today = timezone.localdate()
        for i, d in enumerate((1, 5, 9, 30)):
            Holiday.objects.create(name=f"H{i}", date=today + timedelta(days=d))
        Holiday.objects.create(name="Past", date=today - timedelta(days=3))
        items = selectors.employee_snapshot(self.alice, today)["holidays"]
        self.assertEqual([(i["holiday"].name, i["days_away"]) for i in items], [("H0", 1), ("H1", 5), ("H2", 9)])


class DashboardPageTests(BaseTest):
    def get(self, user=None, url="dashboard"):
        self.login(user or self.alice_user)
        return self.client.get(reverse(url))

    def test_employee_dashboard_renders_with_employee_number(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "employee_dashboard.html")
        self.assertContains(r, self.alice.employee_number)

    def test_punch_buttons_follow_the_state_of_the_day(self):
        def disabled(html, action):
            import re
            m = re.search(r'<button[^>]*value="%s"[^>]*>' % action, html)
            return "disabled" in m.group(0)

        html = self.get().content.decode()
        self.assertEqual((disabled(html, "check_in"), disabled(html, "check_out")), (False, True))
        self.client.post(reverse("attendance_today"), {"action": "check_in"})
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertEqual((disabled(html, "check_in"), disabled(html, "check_out")), (True, False))
        Attendance.objects.filter(emp=self.alice, date=timezone.localdate()).update(check_in=time(0, 0))  # a real gap
        self.client.post(reverse("attendance_today"), {"action": "check_out"})
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertEqual((disabled(html, "check_in"), disabled(html, "check_out")), (True, True))
        self.assertContains(self.client.get(reverse("dashboard")), "Day complete")

    def test_hr_marked_leave_explains_why_punching_is_off(self):
        Attendance.objects.create(emp=self.alice, date=timezone.localdate(), status="L")
        r = self.get()
        self.assertContains(r, "On Approved Leave")
        self.assertContains(r, "punching is not needed")

    def test_inactive_employee_sees_a_clear_state_and_no_action_buttons(self):
        Employee.objects.filter(pk=self.alice.pk).update(employment_status="INACTIVE", is_active=False)
        r = self.get()
        self.assertContains(r, "Record inactive")
        self.assertContains(r, "Contact HR")
        self.assertNotContains(r, reverse("leave_apply"))

    def test_empty_states(self):
        Attendance.objects.all().delete()
        Salary.objects.all().delete()
        r = self.get()
        for text in ("No attendance records yet", "No payslips yet", "No leave requests yet", "No upcoming holidays"):
            self.assertContains(r, text)

    def test_payslips_listed_are_only_the_employees_own(self):
        r = self.get()
        self.assertContains(r, reverse("slip", args=[self.alice_slip.pk]))
        self.assertNotContains(r, reverse("slip", args=[self.bob_slip.pk]))

    def test_month_figures_on_the_page_match_the_records(self):
        today = timezone.localdate()
        Attendance.objects.filter(emp=self.alice).delete()
        att(self.alice, today, "P", time(9), time(17, 30))
        r = self.get()
        month = r.context["month"]
        self.assertEqual((month["present"], month["hours"]), (1, 8.5))
        self.assertContains(r, "<b>8.5</b>", html=False)

    def test_loading_state_script_is_present_and_the_form_posts_own_id(self):
        r = self.get()
        self.assertContains(r, "aria-busy")
        self.assertContains(r, 'name="emp_id" value="%s"' % self.alice.pk)

    def test_notification_is_shown_on_the_page(self):
        att(self.alice, timezone.localdate() - timedelta(days=1), "P", time(9))
        self.assertContains(self.get(), "did not punch out")

    def test_query_count_stays_bounded(self):
        for m in range(2, 8):
            Salary.objects.create(emp=self.alice, month=m, year=2026, gross=1, net=1)
        self.login(self.alice_user)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse("dashboard"))
        self.assertLessEqual(len(ctx), 35, [q["sql"][:70] for q in ctx])

    def test_pages_are_mobile_friendly(self):
        r = self.get()
        self.assertContains(r, 'name="viewport"')
        self.assertContains(r, 'class="scroll"')  # tables scroll inside their own box


class MyPortalTests(BaseTest):
    def test_hr_staff_with_a_profile_keep_the_company_dashboard_and_can_open_their_own_portal(self):
        hr_user = self.hr_admin
        Employee.objects.create(user=hr_user, name="HR Person", designation="HR", basic_salary=Decimal("1"),
                                joined_on=date(2025, 1, 1))
        self.login(hr_user)
        self.assertTemplateUsed(self.client.get(reverse("dashboard")), "dashboard.html")
        r = self.client.get(reverse("my_portal"))
        self.assertTemplateUsed(r, "employee_dashboard.html")
        self.assertContains(r, "HR Person")
        self.assertContains(self.client.get(reverse("dashboard")), reverse("my_portal"))  # nav link

    def test_account_without_a_profile_cannot_open_it(self):
        self.login(self.nobody)
        self.assertEqual(self.client.get(reverse("my_portal")).status_code, 403)

    def test_requires_login(self):
        self.assertEqual(self.client.get(reverse("my_portal")).status_code, 302)

    def test_inactive_employee_can_still_look_at_their_history(self):
        Employee.objects.filter(pk=self.alice.pk).update(employment_status="INACTIVE", is_active=False)
        self.login(self.alice_user)
        self.assertEqual(self.client.get(reverse("my_portal")).status_code, 200)


class FullPunchFlowTests(BaseTest):
    def test_punch_in_then_out_shows_recorded_times_on_the_dashboard(self):
        self.login(self.alice_user)
        self.client.post(reverse("attendance_today"), {"action": "check_in"})
        rec = Attendance.objects.get(emp=self.alice, date=timezone.localdate())
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "Recorded: %s" % rec.check_in.strftime("%H:%M"))
        self.assertContains(r, "Working")
