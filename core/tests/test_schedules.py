from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core import timeutil
from core.models import Attendance, AttendanceAudit, Department, Employee, Salary, WorkLocation, WorkSchedule
from core.services import attendance as att
from core.services import payroll
from core.services import schedules as sch

from .test_permissions import BaseTest

MON = date(2026, 1, 5)    # Monday, a working day
SAT = date(2026, 1, 10)   # Saturday, a rest day under Mon-Fri


def schedule(name="Day", start=time(9), end=time(18), **kw):
    kw.setdefault("break_minutes", 60)
    return WorkSchedule.objects.create(name=name, start_time=start, end_time=end, **kw)


def person(name="Ann", salary="31000", **kw):
    kw.setdefault("joined_on", date(2025, 1, 1))
    return Employee.objects.create(name=name, designation="x", basic_salary=Decimal(salary), **kw)


def day(emp, d, cin=None, cout=None, status="P", next_day=False):
    return Attendance.objects.create(emp=emp, date=d, status=status, check_in=cin, check_out=cout, ends_next_day=next_day)


class ResolutionTests(TestCase):
    def test_employee_then_department_then_default_then_nothing(self):
        mine, dept_s, default = schedule("Mine"), schedule("Dept"), schedule("Company", is_default=True)
        dept = Department.objects.create(name="Ops", code="OPS", work_schedule=dept_s)
        r = sch.ScheduleResolver()
        self.assertEqual(r.for_employee(person("A", work_schedule=mine, department=dept)), mine)
        self.assertEqual(r.for_employee(person("B", department=dept)), dept_s)
        self.assertEqual(r.for_employee(person("C")), default)
        WorkSchedule.objects.update(is_default=False)
        self.assertIsNone(sch.ScheduleResolver().for_employee(person("D")))

    def test_inactive_default_is_ignored_and_only_one_default_is_allowed(self):
        schedule("Old", is_default=True, is_active=False)
        self.assertIsNone(sch.resolve_schedule(person()))      # inactive: not used as the fallback
        with self.assertRaises(IntegrityError), transaction.atomic():
            schedule("B", is_default=True)                       # but it still holds the single "default" slot

    def test_resolver_loads_the_default_once(self):
        schedule(is_default=True)
        people = [person(f"P{i}") for i in range(5)]
        r = sch.ScheduleResolver()
        with self.assertNumQueries(1):
            for p in people:
                r.for_employee(p)


class LatenessAndEarlyTests(TestCase):
    def setUp(self):
        self.s = schedule(grace_minutes=10, early_leave_grace_minutes=5)
        self.e = person(work_schedule=self.s)

    def ev(self, cin, cout=None, d=MON):
        return sch.evaluate(day(self.e, d, cin, cout), self.s)

    def test_within_grace_is_on_time_and_beyond_is_late_from_the_start(self):
        self.assertEqual(self.ev(time(9, 10)).late_minutes, 0)
        Attendance.objects.all().delete()
        self.assertEqual(self.ev(time(9, 11)).late_minutes, 11)  # measured from 09:00, not from the grace limit
        Attendance.objects.all().delete()
        self.assertEqual(self.ev(time(8, 30)).late_minutes, 0)   # early arrival

    def test_early_departure(self):
        self.assertEqual(self.ev(time(9), time(17, 55)).early_minutes, 0)  # inside the 5 min grace
        Attendance.objects.all().delete()
        self.assertEqual(self.ev(time(9), time(17, 30)).early_minutes, 30)

    def test_no_checkout_means_no_early_or_overtime(self):
        r = self.ev(time(9, 30))
        self.assertEqual((r.late_minutes, r.early_minutes, r.overtime_minutes, r.worked_minutes), (30, 0, 0, None))

    def test_worked_time_excludes_the_break(self):
        self.assertEqual(self.ev(time(9), time(18)).worked_minutes, 8 * 60)  # 9h stay - 60 min break
        Attendance.objects.all().delete()
        self.assertEqual(self.ev(time(9), time(9, 40)).worked_minutes, 40)   # shorter than the break: nothing taken off

    def test_only_present_records_are_evaluated(self):
        for status in ("A", "L", "H"):
            self.assertIsNone(sch.evaluate(Attendance(emp=self.e, date=MON, status=status), self.s))
        self.assertIsNone(sch.evaluate(Attendance(emp=self.e, date=MON, status="P"), None))

    def test_rest_days_have_no_lateness(self):
        r = self.ev(time(14), time(18), d=SAT)
        self.assertEqual((r.working_day, r.late_minutes, r.early_minutes), (False, 0, 0))


class OvertimeRuleTests(TestCase):
    def setUp(self):
        self.s = schedule(overtime_eligible=True, overtime_after_minutes=30, overtime_max_minutes=240)
        self.e = person(work_schedule=self.s)

    def ot(self, cout, d=MON, cin=time(9)):
        Attendance.objects.all().delete()
        return sch.evaluate(day(self.e, d, cin, cout), self.s).overtime_minutes

    def test_threshold_then_counts_in_full(self):
        self.assertEqual(self.ot(time(18, 29)), 0)
        self.assertEqual(self.ot(time(18, 30)), 30)
        self.assertEqual(self.ot(time(19, 45)), 105)

    def test_daily_cap_guards_a_forgotten_punch_out(self):
        self.assertEqual(self.ot(time(23, 50)), 240)

    def test_zero_cap_means_unlimited_and_zero_threshold_counts_any_extra_minute(self):
        WorkSchedule.objects.filter(pk=self.s.pk).update(overtime_max_minutes=0, overtime_after_minutes=0)
        self.s.refresh_from_db()
        self.assertEqual(self.ot(time(23, 50)), 350)
        self.assertEqual(self.ot(time(18, 1)), 1)
        self.assertEqual(self.ot(time(18, 0)), 0)

    def test_early_arrival_earns_nothing(self):
        self.assertEqual(self.ot(time(18), cin=time(6)), 0)

    def test_not_eligible_means_none(self):
        WorkSchedule.objects.filter(pk=self.s.pk).update(overtime_eligible=False)
        self.s.refresh_from_db()
        self.assertEqual(self.ot(time(21)), 0)

    def test_rest_day_work_counts_as_overtime_up_to_twelve_hours(self):
        self.assertEqual(self.ot(time(14), d=SAT, cin=time(9)), 4 * 60)          # 5h stay, 60 min break
        self.assertEqual(self.ot(time(23, 59), d=SAT, cin=time(0, 1)), 12 * 60)  # capped


class OvernightTests(TestCase):
    def setUp(self):
        self.s = schedule("Night", time(22), time(6), break_minutes=30, grace_minutes=5, overtime_eligible=True)
        self.e = person("Nina", work_schedule=self.s)

    def test_schedule_knows_it_is_overnight(self):
        self.assertTrue(self.s.is_overnight)
        self.assertFalse(schedule("Plain").is_overnight)
        self.assertEqual(sch.scheduled_minutes(self.s), 8 * 60 - 30)  # 22:00 -> 06:00 minus break

    def test_durations_are_positive_and_correct(self):
        rec = day(self.e, MON, time(22, 0), time(6, 0), next_day=True)
        self.assertEqual(rec.hours, 8.0)
        r = sch.evaluate(rec, self.s)
        self.assertEqual((r.worked_minutes, r.late_minutes, r.early_minutes, r.overtime_minutes), (8 * 60 - 30, 0, 0, 0))

    def test_late_and_overtime_are_measured_against_the_shift_across_midnight(self):
        rec = day(self.e, MON, time(22, 20), time(7, 15), next_day=True)
        r = sch.evaluate(rec, self.s)
        self.assertEqual((r.late_minutes, r.overtime_minutes), (20, 75))
        self.assertEqual(rec.hours, 8.92)

    def test_leaving_early_across_midnight(self):
        rec = day(self.e, MON, time(22), time(5, 0), next_day=True)
        self.assertEqual(sch.evaluate(rec, self.s).early_minutes, 60)

    def test_without_the_flag_an_earlier_checkout_is_invalid(self):
        rec = Attendance(emp=self.e, date=MON, check_in=time(22), check_out=time(6))
        with self.assertRaises(ValidationError) as ctx:
            rec.clean()
        self.assertIn("overnight", str(ctx.exception).lower())

    def test_flag_rules_in_model_and_database(self):
        ok = Attendance(emp=self.e, date=MON, check_in=time(22), check_out=time(6), ends_next_day=True)
        ok.clean()
        for bad in (Attendance(emp=self.e, date=MON, check_in=time(6), check_out=time(22), ends_next_day=True),   # >= 24h
                    Attendance(emp=self.e, date=MON, check_in=time(22), ends_next_day=True)):                      # no check-out
            with self.assertRaises(ValidationError):
                bad.clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(emp=self.e, date=MON, check_in=time(6), check_out=time(22), ends_next_day=True)

    # --- the punch flow ---
    def test_punch_in_evening_punch_out_next_morning(self):
        att.check_in(self.e, now=time(22, 3), today=MON)
        self.assertEqual(att.current_record(self.e, MON + timedelta(days=1)).date, MON)  # still open this morning
        rec = att.check_out(self.e, now=time(6, 10), today=MON + timedelta(days=1))
        self.assertEqual((rec.date, rec.ends_next_day, rec.check_out), (MON, True, time(6, 10)))
        self.assertEqual(Attendance.objects.count(), 1)  # no stray record on the second day
        self.assertEqual(AttendanceAudit.objects.filter(action="CHECK_OUT").get().new_values["ends_next_day"], True)

    def test_second_check_out_is_refused(self):
        att.check_in(self.e, now=time(22), today=MON)
        att.check_out(self.e, now=time(6), today=MON + timedelta(days=1))
        with self.assertRaises(att.AttendanceError) as ctx:
            att.check_out(self.e, now=time(7), today=MON + timedelta(days=1))
        self.assertEqual(ctx.exception.code, "not_checked_in")

    def test_a_shift_of_24_hours_or_more_is_refused(self):
        att.check_in(self.e, now=time(22), today=MON)
        with self.assertRaises(att.AttendanceError) as ctx:
            att.check_out(self.e, now=time(23), today=MON + timedelta(days=1))
        self.assertEqual(ctx.exception.code, "invalid_time")
        self.assertIsNone(Attendance.objects.get().check_out)

    def test_day_shift_workers_never_attach_to_yesterday(self):
        plain = person("Dan", work_schedule=schedule("Plain"))
        att.check_in(plain, now=time(9), today=MON)
        with self.assertRaises(att.AttendanceError) as ctx:
            att.check_out(plain, now=time(6), today=MON + timedelta(days=1))
        self.assertEqual(ctx.exception.code, "not_checked_in")

    def test_marking_absent_clears_the_overnight_flag(self):
        day(self.e, MON, time(22), time(6), next_day=True)
        rec = att.mark_status(self.e, "A", today=MON, reason="did not work")
        self.assertEqual((rec.check_out, rec.ends_next_day), (None, False))


class TimeZoneTests(TestCase):
    NOW = datetime(2026, 1, 5, 23, 30, tzinfo=dt_timezone.utc)  # 23:30 UTC

    def test_validator_accepts_real_zones_and_rejects_nonsense(self):
        WorkLocation(name="L", time_zone="Africa/Lagos").full_clean()
        WorkLocation(name="M", time_zone="").full_clean()
        for bad in ("Mars/Base", "lagos", "UTC+1"):
            with self.assertRaises(ValidationError, msg=bad):
                WorkLocation(name="X", time_zone=bad).full_clean()

    def test_each_employee_gets_their_own_local_day(self):
        lagos = WorkLocation.objects.create(name="Lagos", time_zone="Africa/Lagos")      # UTC+1  -> 00:30 on the 6th
        auckland = WorkLocation.objects.create(name="Auckland", time_zone="Pacific/Auckland")  # UTC+13 -> 12:30 on the 6th
        la = WorkLocation.objects.create(name="LA", time_zone="America/Los_Angeles")     # UTC-8  -> 15:30 on the 5th
        with mock.patch("core.timeutil.timezone.now", return_value=self.NOW):
            self.assertEqual(timeutil.today_for(person("A", work_location=lagos)), date(2026, 1, 6))
            self.assertEqual(timeutil.time_for(person("B", work_location=auckland)), time(12, 30))
            self.assertEqual(timeutil.today_for(person("C", work_location=la)), date(2026, 1, 5))
            self.assertEqual(timeutil.today_for(person("D")).isoformat() in ("2026-01-05", "2026-01-06"), True)  # company zone

    def test_blank_location_zone_falls_back_to_the_company_zone(self):
        loc = WorkLocation.objects.create(name="HQ")
        with self.settings(TIME_ZONE="Africa/Lagos"), mock.patch("core.timeutil.timezone.now", return_value=self.NOW):
            self.assertEqual(timeutil.today_for(person("A", work_location=loc)), date(2026, 1, 6))

    def test_punching_uses_the_employees_zone_when_no_time_is_given(self):
        lagos = WorkLocation.objects.create(name="Lagos", time_zone="Africa/Lagos")
        e = person("A", work_location=lagos)
        with mock.patch("core.timeutil.timezone.now", return_value=self.NOW):
            rec = att.check_in(e)
        self.assertEqual((rec.date, rec.check_in), (date(2026, 1, 6), time(0, 30)))

    def test_the_future_date_rule_uses_the_employees_zone(self):
        auckland = WorkLocation.objects.create(name="Auckland", time_zone="Pacific/Auckland")
        e = person("A", work_location=auckland, joined_on=date(2025, 1, 1))
        with mock.patch("core.timeutil.timezone.now", return_value=self.NOW):
            Attendance(emp=e, date=date(2026, 1, 6)).clean()           # already the 6th in Auckland
            with self.assertRaises(ValidationError):
                Attendance(emp=e, date=date(2026, 1, 7)).clean()


class ScheduleValidationTests(TestCase):
    def test_policy_field_rules(self):
        def check(**kw):
            WorkSchedule(**{"name": "S", "start_time": time(9), "end_time": time(17), **kw}).full_clean(
                validate_unique=False, validate_constraints=False)

        check()
        with self.assertRaises(ValidationError):
            check(overtime_after_minutes=120, overtime_max_minutes=60)
        with self.assertRaises(ValidationError):
            check(overtime_multiplier=Decimal("0.5"))
        with self.assertRaises(ValidationError):
            check(end_time=time(9))


class OvertimePayTests(TestCase):
    """January has 31 days: basic 31000 -> 1000 per day; an 8 h paid shift -> 125 per hour."""

    def setUp(self):
        self.s = schedule(overtime_eligible=True, overtime_multiplier=Decimal("1.50"))
        self.e = person("Olu", work_schedule=self.s)

    def row(self, e=None, m=1, y=2026):
        e = e or self.e
        return next(r for r in payroll.calculate_payroll_rows(m, y) if r["emp"].pk == e.pk)

    def test_exact_figures(self):
        day(self.e, MON, time(9), time(19, 30))                  # 90 min after 18:00
        r = self.row()
        self.assertEqual((r["overtime_minutes"], r["overtime_pay"]), (90, Decimal("281.25")))   # 1.5 h x 125 x 1.5
        self.assertEqual((r["deduction"], r["net"]), (Decimal("0.00"), Decimal("31281.25")))

    def test_minutes_add_up_over_the_month_and_the_multiplier_is_applied(self):
        WorkSchedule.objects.filter(pk=self.s.pk).update(overtime_multiplier=Decimal("2.00"))
        day(self.e, MON, time(9), time(19))
        day(self.e, MON + timedelta(days=1), time(9), time(20))
        r = self.row()
        self.assertEqual((r["overtime_minutes"], r["overtime_pay"]), (180, Decimal("750.00")))  # 3 h x 125 x 2

    def test_overtime_comes_on_top_of_absence_deductions(self):
        day(self.e, MON, time(9), time(19, 30))
        day(self.e, MON + timedelta(days=1), None, None, status="A")
        r = self.row()
        self.assertEqual((r["deduction"], r["overtime_pay"], r["net"]),
                         (Decimal("1000.00"), Decimal("281.25"), Decimal("30281.25")))

    def test_daily_cap_limits_what_is_paid(self):
        day(self.e, MON, time(9), time(23, 45))
        self.assertEqual(self.row()["overtime_minutes"], 240)

    def test_overnight_overtime_is_paid(self):
        night = schedule("Night", time(22), time(6), overtime_eligible=True, break_minutes=0)
        n = person("Nina", work_schedule=night)
        day(n, MON, time(22), time(7, 30), next_day=True)   # 90 min after 06:00; 8 h shift -> 125/h
        r = self.row(n)
        self.assertEqual((r["overtime_minutes"], r["overtime_pay"]), (90, Decimal("281.25")))

    def test_rest_day_work_is_paid_as_overtime(self):
        day(self.e, SAT, time(9), time(14))   # 5h stay - 60 min break = 4 h
        self.assertEqual(self.row()["overtime_pay"], Decimal("750.00"))  # 4 x 125 x 1.5

    def test_only_the_selected_month_counts(self):
        day(self.e, date(2026, 2, 2), time(9), time(20))
        self.assertEqual(self.row()["overtime_minutes"], 0)
        self.assertEqual(self.row(m=2)["overtime_minutes"], 120)

    def test_generation_stores_overtime_and_keeps_net_equal_to_gross_minus_deduction(self):
        day(self.e, MON, time(9), time(19, 30))
        day(self.e, MON + timedelta(days=1), status="A")
        payroll.generate_payroll(1, 2026)
        payroll.generate_payroll(1, 2026)  # repeatable
        s = Salary.objects.get(emp=self.e, month=1, year=2026)
        self.assertEqual((s.overtime_minutes, s.overtime_pay, s.gross, s.deduction, s.net),
                         (90, Decimal("281.25"), Decimal("31281.25"), Decimal("1000.00"), Decimal("30281.25")))
        self.assertEqual(s.net, s.gross - s.deduction)
        self.assertEqual(Salary.objects.count(), 1)


class PayrollUnchangedWithoutOvertimeTests(TestCase):
    """The rule: no overtime -> payroll is exactly what it always was."""

    def reference(self, e, absent, half, days):
        """The original formula, written out independently of the service."""
        per_day = (e.basic_salary / days).quantize(Decimal("0.01"))
        absent_days = Decimal(absent) + Decimal(half) / 2
        deduction = min((absent_days * per_day).quantize(Decimal("0.01")), e.basic_salary)
        return per_day, absent_days, deduction, e.basic_salary - deduction

    def test_every_kind_of_employee_without_overtime_matches_the_original_formula(self):
        eligible = schedule("Elig", overtime_eligible=True)
        plain = schedule("Plain", overtime_eligible=False)
        schedule("Default", is_default=True, overtime_eligible=True)  # used by employees with no schedule
        cases = {
            "no schedule": person("A", "31000", work_schedule=None),
            "not eligible, stays very late": person("B", "27777", work_schedule=plain),
            "eligible, exactly on time": person("C", "10000", work_schedule=eligible),
            "eligible, leaves before the end": person("D", "45678.91", work_schedule=eligible),
            "eligible, below the 30 min threshold": person("E", "31000", work_schedule=eligible),
            "eligible but never punched out": person("F", "31000", work_schedule=eligible),
            "company default, no overtime": person("G", "1", work_schedule=None),
            "eligible, only absences": person("H", "31000", work_schedule=eligible),
        }
        # (employee, per-day attendance lines)
        for name, e in cases.items():
            day(e, MON, time(9), time(17, 59) if name.startswith("eligible, leaves") else time(18))
        day(cases["not eligible, stays very late"], MON + timedelta(days=1), time(9), time(23))
        day(cases["eligible, below the 30 min threshold"], MON + timedelta(days=1), time(9), time(18, 29))
        day(cases["eligible but never punched out"], MON + timedelta(days=1), time(9), None)
        day(cases["eligible, only absences"], MON + timedelta(days=2), status="A")
        day(cases["eligible, only absences"], MON + timedelta(days=3), status="H")
        for r in payroll.calculate_payroll_rows(1, 2026):
            e = r["emp"]
            per_day, absent_days, deduction, net = self.reference(e, r["absent"], r["half"], 31)
            label = e.name
            self.assertEqual((r["per_day"], r["absent_days"], r["deduction"], r["net"]),
                             (per_day, absent_days, deduction, net), label)
            self.assertEqual(str(r["net"]), str(net), label)  # not even a trailing digit differs
            self.assertEqual((r["overtime_minutes"], r["overtime_pay"]), (0, Decimal("0.00")), label)

    def test_stored_payslips_for_such_employees_are_unchanged_too(self):
        e = person("Z", "31000", work_schedule=schedule(overtime_eligible=True))
        day(e, MON, time(9), time(18))
        day(e, MON + timedelta(days=1), status="A")
        payroll.generate_payroll(1, 2026)
        s = Salary.objects.get(emp=e)
        self.assertEqual((s.gross, s.deduction, s.net, s.overtime_minutes, s.overtime_pay),
                         (Decimal("31000.00"), Decimal("1000.00"), Decimal("30000.00"), 0, Decimal("0.00")))


class OvertimeScreensTests(BaseTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.s = schedule("Std", grace_minutes=10, overtime_eligible=True)
        Employee.objects.filter(pk=cls.alice.pk).update(work_schedule=cls.s, joined_on=date(2025, 1, 1))
        Attendance.objects.filter(emp=cls.alice).delete()
        Salary.objects.all().delete()
        day(Employee.objects.get(pk=cls.alice.pk), MON, time(9, 25), time(19, 30))

    def test_attendance_log_tags_lateness_and_overtime(self):
        self.login(self.hr_admin)
        r = self.client.get(reverse("attendance_list"), {"month": 1, "year": 2026})
        self.assertContains(r, "Late 25 m")
        self.assertContains(r, "OT 1 h 30 m")

    def test_overnight_records_are_marked_in_the_log(self):
        night = schedule("Night", time(22), time(6))
        Employee.objects.filter(pk=self.bob.pk).update(work_schedule=night, joined_on=date(2025, 1, 1))
        day(Employee.objects.get(pk=self.bob.pk), MON, time(22), time(6), next_day=True)
        self.login(self.hr_admin)
        self.assertContains(self.client.get(reverse("attendance_list"), {"month": 1, "year": 2026}), "(+1 day)")

    def test_payroll_register_and_payslip_show_overtime_only_when_there_is_some(self):
        self.login(self.hr_admin)
        url = reverse("payroll") + "?month=1&year=2026"
        self.client.post(url)
        r = self.client.get(url)
        self.assertContains(r, "<th>Overtime</th>")
        slip = Salary.objects.get(emp=self.alice, month=1, year=2026)
        page = self.client.get(reverse("slip", args=[slip.pk]))
        self.assertContains(page, "Overtime (1 h 30 m)")
        self.assertContains(page, "Gross pay")
        self.assertEqual(slip.overtime_minutes, 90)

    def test_payslip_layout_is_unchanged_without_overtime(self):
        self.login(self.hr_admin)
        url = reverse("payroll") + "?month=2&year=2026"  # no attendance in February
        self.client.post(url)
        self.assertNotContains(self.client.get(url), "<th>Overtime</th>")
        slip = Salary.objects.filter(month=2, year=2026).first()
        page = self.client.get(reverse("slip", args=[slip.pk]))
        self.assertContains(page, "Basic salary")
        self.assertNotContains(page, "Overtime")

    def test_employee_dashboard_shows_lateness_and_overtime(self):
        with mock.patch("core.selectors.schedules.resolve_schedule", return_value=self.s):
            from core import selectors
            m = selectors.month_summary(Employee.objects.get(pk=self.alice.pk), MON)
        self.assertEqual((m["late_days"], m["late_minutes"], m["overtime_minutes"], m["overtime_text"]), (1, 25, 90, "1 h 30 m"))

    def test_hr_can_manage_schedules_with_the_new_fields_in_the_admin(self):
        self.client.force_login(self.superuser)
        r = self.client.get(reverse("admin:core_workschedule_change", args=[self.s.pk]))
        for field in ("overtime_eligible", "overtime_multiplier", "is_default", "early_leave_grace_minutes"):
            self.assertContains(r, f'name="{field}"')
        r = self.client.get(reverse("admin:core_workschedule_change", args=[self.s.pk]))
        self.assertContains(r, "Overtime pay rate as a multiple")
