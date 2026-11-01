import threading
from datetime import date, time, timedelta
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from core.models import Attendance, AttendanceAudit, Employee
from core.services import attendance as svc
from core.services.attendance import AttendanceError

from .test_permissions import BaseTest, make_user

DAY = date(2026, 3, 18)


def mk(name="Ann", **kw):
    kw.setdefault("joined_on", date(2025, 1, 1))
    return Employee.objects.create(name=name, designation="x", basic_salary=Decimal("1000"), **kw)


def code_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except AttendanceError as exc:
        return exc.code
    return None


class PunchTransitionTests(TestCase):
    def setUp(self):
        self.e = mk()

    def test_normal_day(self):
        svc.check_in(self.e, now=time(9), today=DAY)
        rec = svc.check_out(self.e, now=time(17, 30), today=DAY)
        self.assertEqual((rec.status, rec.check_in, rec.check_out), ("P", time(9), time(17, 30)))

    def test_duplicate_check_in_is_refused_and_changes_nothing(self):
        svc.check_in(self.e, now=time(9), today=DAY)
        self.assertEqual(code_of(svc.check_in, self.e, now=time(9, 30), today=DAY), "already_checked_in")
        rec = Attendance.objects.get(emp=self.e, date=DAY)
        self.assertEqual(rec.check_in, time(9))
        self.assertEqual(AttendanceAudit.objects.filter(emp=self.e).count(), 1)

    def test_check_out_without_check_in_is_refused_and_creates_no_row(self):
        self.assertEqual(code_of(svc.check_out, self.e, now=time(17), today=DAY), "not_checked_in")
        self.assertFalse(Attendance.objects.exists())

    def test_duplicate_check_out_is_refused(self):
        svc.check_in(self.e, now=time(9), today=DAY)
        svc.check_out(self.e, now=time(17), today=DAY)
        self.assertEqual(code_of(svc.check_out, self.e, now=time(18), today=DAY), "already_checked_out")
        self.assertEqual(Attendance.objects.get(emp=self.e).check_out, time(17))

    def test_check_out_must_be_after_check_in(self):
        svc.check_in(self.e, now=time(9), today=DAY)
        for bad in (time(8, 59), time(9)):
            self.assertEqual(code_of(svc.check_out, self.e, now=bad, today=DAY), "invalid_time")
        self.assertIsNone(Attendance.objects.get(emp=self.e).check_out)

    def test_hint_when_yesterday_was_left_open(self):
        svc.check_in(self.e, now=time(9), today=DAY - timedelta(days=1))
        with self.assertRaisesMessage(AttendanceError, "earlier day without a check-out"):
            svc.check_out(self.e, now=time(9), today=DAY)

    def test_leave_absent_and_half_day_block_punching(self):
        for status in ("L", "A", "H"):
            e = mk(f"E{status}")
            svc.mark_status(e, status, today=DAY)
            self.assertEqual(code_of(svc.check_in, e, now=time(9), today=DAY), "status_locked", status)
            self.assertEqual(code_of(svc.check_out, e, now=time(17), today=DAY), "not_checked_in", status)

    def test_empty_present_entry_created_by_hr_can_be_punched_into(self):
        Attendance.objects.create(emp=self.e, date=DAY, status="P")
        rec = svc.check_in(self.e, now=time(9, 15), today=DAY)
        self.assertEqual(rec.check_in, time(9, 15))
        self.assertEqual(AttendanceAudit.objects.get().old_values["check_in"], None)

    def test_inactive_employee_cannot_punch(self):
        self.e.employment_status = "INACTIVE"
        self.e.save()
        for fn in (svc.check_in, svc.check_out):
            self.assertEqual(code_of(fn, self.e, now=time(9), today=DAY), "inactive_employee")


class StatusChangeTests(TestCase):
    def setUp(self):
        self.e = mk()

    def test_new_status_creates_a_row(self):
        for status in ("A", "H", "L"):
            e = mk(f"X{status}")
            self.assertEqual(svc.mark_status(e, status, today=DAY).status, status)

    def test_same_status_again_is_a_harmless_no_op(self):
        svc.mark_status(self.e, "A", today=DAY)
        svc.mark_status(self.e, "A", today=DAY)
        self.assertEqual(AttendanceAudit.objects.count(), 1)

    def test_changing_between_statuses_without_punches_is_fine(self):
        svc.mark_status(self.e, "A", today=DAY)
        self.assertEqual(svc.mark_status(self.e, "L", today=DAY).status, "L")
        self.assertEqual(svc.mark_status(self.e, "H", today=DAY).status, "H")

    def test_erasing_real_punches_needs_a_reason(self):
        svc.check_in(self.e, now=time(9), today=DAY)
        self.assertEqual(code_of(svc.mark_status, self.e, "A", today=DAY), "correction_required")
        self.assertEqual(code_of(svc.mark_status, self.e, "L", today=DAY, reason="   "), "correction_required")
        self.assertEqual(Attendance.objects.get().check_in, time(9))  # untouched
        rec = svc.mark_status(self.e, "L", today=DAY, reason="approved retroactively")
        self.assertEqual((rec.status, rec.check_in), ("L", None))
        audit = AttendanceAudit.objects.filter(action="SET_STATUS").get()
        self.assertEqual((audit.old_values["check_in"], audit.new_values["status"], audit.reason),
                         ("09:00:00", "L", "approved retroactively"))

    def test_half_day_keeps_punches(self):
        svc.check_in(self.e, now=time(9), today=DAY)
        self.assertEqual(svc.mark_status(self.e, "H", today=DAY).check_in, time(9))

    def test_unsupported_status_is_a_programming_error(self):
        for bad in ("P", "X", "", None):
            with self.assertRaises(ValueError):
                svc.mark_status(self.e, bad, today=DAY)


class AuditTrailTests(TestCase):
    def test_every_punch_is_recorded_with_who(self):
        e, user = mk(), make_user("hr1")
        svc.check_in(e, now=time(9), today=DAY, by=user)
        svc.check_out(e, now=time(17), today=DAY, by=user)
        rows = list(AttendanceAudit.objects.order_by("id"))
        self.assertEqual([r.action for r in rows], ["CHECK_IN", "CHECK_OUT"])
        self.assertEqual((rows[0].old_values, rows[0].new_values["check_in"], rows[0].changed_by), (None, "09:00:00", user))
        self.assertEqual((rows[1].old_values["check_out"], rows[1].new_values["check_out"]), (None, "17:00:00"))

    def test_audit_rows_cannot_be_edited(self):
        e = mk()
        svc.check_in(e, now=time(9), today=DAY)
        row = AttendanceAudit.objects.get()
        row.reason = "tampered"
        with self.assertRaises(Exception):
            row.save()

    def test_employee_with_audit_history_cannot_be_deleted(self):
        e = mk()
        svc.check_in(e, now=time(9), today=DAY)
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            e.delete()


class DatabaseConstraintTests(TestCase):
    def fails(self, **kw):
        e = mk(kw.pop("name", "C"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(emp=e, date=DAY, **kw)

    def test_checkout_without_checkin_is_impossible_even_via_orm_create(self):
        self.fails(status="P", check_out=time(17))

    def test_absent_or_leave_with_punches_is_impossible(self):
        self.fails(name="A1", status="A", check_in=time(9))
        self.fails(name="L1", status="L", check_in=time(9), check_out=time(17))

    def test_valid_rows_are_fine(self):
        e = mk()
        Attendance.objects.create(emp=e, date=DAY, status="H", check_in=time(9))
        Attendance.objects.create(emp=e, date=DAY - timedelta(days=1), status="A")


class ModelCleanTests(BaseTest):
    def row(self, **kw):
        data = {"emp": self.alice, "date": timezone.localdate(), "status": "P", **kw}
        return Attendance(**data)

    def errors(self, rec):
        from django.core.exceptions import ValidationError
        try:
            rec.clean()
        except ValidationError as exc:
            return exc.message_dict
        return {}

    def test_valid_row(self):
        self.assertEqual(self.errors(self.row(check_in=time(9), check_out=time(17))), {})

    def test_future_date_before_joining_and_after_leaving(self):
        self.assertIn("date", self.errors(self.row(date=timezone.localdate() + timedelta(days=1))))
        self.assertIn("joined", self.errors(self.row(date=self.alice.joined_on - timedelta(days=1)))["date"][0])
        Employee.objects.filter(pk=self.alice.pk).update(
            employment_status="RESIGNED", is_active=False, separation_date=self.alice.joined_on)
        left = Employee.objects.get(pk=self.alice.pk)
        self.assertIn("left", self.errors(self.row(emp=left, date=left.joined_on + timedelta(days=1)))["date"][0])

    def test_time_rules(self):
        self.assertIn("check_out", self.errors(self.row(check_out=time(17))))
        self.assertIn("check_out", self.errors(self.row(check_in=time(9), check_out=time(9))))
        self.assertIn("check_out", self.errors(self.row(check_in=time(10), check_out=time(9))))
        self.assertIn("status", self.errors(self.row(status="A", check_in=time(9))))


class CorrectionFormTests(BaseTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Employee.objects.filter(pk=cls.alice.pk).update(joined_on=date(2025, 1, 1))

    def payload(self, **kw):
        return {"emp": self.alice.pk, "date": "2026-02-10", "status": "P", "check_in": "09:00", "check_out": "17:00",
                "reason": "forgot to punch", **kw}

    def test_reason_is_mandatory(self):
        self.login(self.hr_officer)
        r = self.client.post(reverse("attendance_add"), self.payload(reason=""))
        self.assertEqual(r.status_code, 200)
        self.assertIn("reason", r.context["form"].errors)
        self.assertFalse(Attendance.objects.filter(date=date(2026, 2, 10)).exists())

    def test_manual_entry_is_saved_and_audited(self):
        self.login(self.hr_officer)
        self.assertEqual(self.client.post(reverse("attendance_add"), self.payload()).status_code, 302)
        audit = AttendanceAudit.objects.get(action="MANUAL_CREATE")
        self.assertEqual((audit.reason, audit.changed_by, audit.new_values["check_out"]), ("forgot to punch", self.hr_officer, "17:00:00"))

    def test_invalid_entries_are_rejected_with_messages(self):
        self.login(self.hr_officer)
        for bad in ({"check_out": "08:00"}, {"status": "A"}, {"date": "2999-01-01"}, {"date": "2020-01-01"}):
            r = self.client.post(reverse("attendance_add"), self.payload(**bad))
            self.assertEqual(r.status_code, 200, bad)
            self.assertTrue(r.context["form"].errors, bad)
        self.assertEqual(Attendance.objects.filter(date__in=[date(2026, 2, 10), date(2020, 1, 1)]).count(), 0)

    def test_edit_records_before_and_after(self):
        rec = Attendance.objects.create(emp=self.alice, date=date(2026, 2, 10), status="P", check_in=time(9), check_out=time(17))
        self.login(self.hr_officer)
        r = self.client.post(reverse("attendance_edit", args=[rec.pk]),
                             self.payload(check_out="18:00", reason="stayed late"))
        self.assertEqual(r.status_code, 302)
        audit = AttendanceAudit.objects.get(action="MANUAL_EDIT")
        self.assertEqual((audit.old_values["check_out"], audit.new_values["check_out"], audit.reason),
                         ("17:00:00", "18:00:00", "stayed late"))

    def test_saving_without_changes_records_nothing(self):
        rec = Attendance.objects.create(emp=self.alice, date=date(2026, 2, 10), status="P", check_in=time(9), check_out=time(17))
        self.login(self.hr_officer)
        self.client.post(reverse("attendance_edit", args=[rec.pk]), self.payload())
        self.assertFalse(AttendanceAudit.objects.exists())

    def test_delete_needs_a_reason_and_keeps_history(self):
        rec = Attendance.objects.create(emp=self.alice, date=date(2026, 2, 10), status="P", check_in=time(9))
        self.login(self.hr_admin)
        url = reverse("attendance_delete", args=[rec.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 200)
        self.assertTrue(Attendance.objects.filter(pk=rec.pk).exists())
        self.assertEqual(self.client.post(url, {"reason": "duplicate entry"}).status_code, 302)
        self.assertFalse(Attendance.objects.filter(pk=rec.pk).exists())
        audit = AttendanceAudit.objects.get(action="MANUAL_DELETE")
        self.assertEqual((audit.reason, audit.date, audit.record_id, audit.attendance, audit.old_values["check_in"]),
                         ("duplicate entry", date(2026, 2, 10), rec.pk, None, "09:00:00"))

    def test_hr_officer_still_cannot_delete(self):
        rec = Attendance.objects.create(emp=self.alice, date=date(2026, 2, 10))
        self.login(self.hr_officer)
        self.assertEqual(self.client.post(reverse("attendance_delete", args=[rec.pk]), {"reason": "x"}).status_code, 403)

    def test_admin_site_cannot_bypass_the_audit(self):
        self.login(self.superuser)
        rec = Attendance.objects.create(emp=self.alice, date=date(2026, 2, 10))
        change = reverse("admin:core_attendance_change", args=[rec.pk])
        self.assertEqual(self.client.post(change, {"status": "A", "emp": self.alice.pk, "date": "2026-02-10"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:core_attendance_delete", args=[rec.pk]), {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:core_attendance_add")).status_code, 403)
        rec.refresh_from_db()
        self.assertEqual(rec.status, "P")  # untouched

    def test_audit_history_permission(self):
        self.assertTrue(self.hr_admin.has_perm("core.view_attendance_audit"))
        for user in (self.hr_officer, self.payroll_officer, self.alice_user):
            self.assertFalse(user.has_perm("core.view_attendance_audit"), user.username)


class PunchViewMessageTests(BaseTest):
    def test_duplicate_punch_shows_a_friendly_message_not_an_error_page(self):
        self.login(self.alice_user)
        self.client.post(reverse("attendance_today"), {"action": "check_in"})
        r = self.client.post(reverse("attendance_today"), {"action": "check_in"}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Already punched in today")
        self.assertEqual(Attendance.objects.filter(emp=self.alice, date=timezone.localdate()).count(), 1)

    def test_check_out_first_shows_a_message(self):
        self.login(self.alice_user)
        r = self.client.post(reverse("attendance_today"), {"action": "check_out"}, follow=True)
        self.assertContains(r, "have not punched in today")

    def test_hr_register_explains_when_a_correction_is_needed(self):
        svc.check_in(self.bob, now=time(0, 1))
        self.login(self.hr_officer)
        r = self.client.post(reverse("attendance_today"), {"emp_id": self.bob.pk, "action": "A"}, follow=True)
        self.assertContains(r, "Use Edit attendance and give a reason")
        self.assertEqual(Attendance.objects.get(emp=self.bob, date=timezone.localdate()).status, "P")

    def test_audit_records_who_punched(self):
        self.login(self.alice_user)
        self.client.post(reverse("attendance_today"), {"action": "check_in"})
        self.assertEqual(AttendanceAudit.objects.get().changed_by, self.alice_user)


@skipUnlessDBFeature("has_select_for_update")
class ConcurrencyTests(TransactionTestCase):
    """Real simultaneous requests against PostgreSQL (skipped on SQLite, which cannot lock rows)."""

    serialized_rollback = True

    def run_parallel(self, n, fn):
        results, barrier = [], threading.Barrier(n)

        def worker():
            try:
                barrier.wait()
                fn()
                results.append("ok")
            except AttendanceError as exc:
                results.append(exc.code)
            except Exception as exc:  # pragma: no cover - would fail the assertions below
                results.append(f"crash:{type(exc).__name__}:{exc}")
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(n)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        return results

    def test_simultaneous_check_ins_create_exactly_one_record(self):
        e = mk()
        results = self.run_parallel(12, lambda: svc.check_in(e, now=time(9), today=DAY))
        self.assertEqual(sorted(results), ["already_checked_in"] * 11 + ["ok"], results)
        self.assertEqual(Attendance.objects.filter(emp=e, date=DAY).count(), 1)
        self.assertEqual(AttendanceAudit.objects.filter(emp=e, action="CHECK_IN").count(), 1)

    def test_simultaneous_check_outs_record_exactly_one(self):
        e = mk()
        svc.check_in(e, now=time(9), today=DAY)
        results = self.run_parallel(12, lambda: svc.check_out(e, now=time(17), today=DAY))
        self.assertEqual(sorted(results), ["already_checked_out"] * 11 + ["ok"], results)
        self.assertEqual(AttendanceAudit.objects.filter(emp=e, action="CHECK_OUT").count(), 1)

    def test_different_employees_do_not_block_each_other(self):
        people = [mk(f"P{i}") for i in range(8)]
        it = iter(people)
        lock = threading.Lock()

        def punch():
            with lock:
                person = next(it)
            svc.check_in(person, now=time(9), today=DAY)

        self.assertEqual(self.run_parallel(8, punch), ["ok"] * 8)
        self.assertEqual(Attendance.objects.filter(date=DAY).count(), 8)

    def test_status_change_racing_with_a_punch_leaves_one_consistent_row(self):
        e = mk()
        calls = [lambda: svc.check_in(e, now=time(9), today=DAY), lambda: svc.mark_status(e, "L", today=DAY)] * 4
        it, lock = iter(calls), threading.Lock()

        def go():
            with lock:
                fn = next(it)
            fn()

        self.run_parallel(8, go)
        rows = Attendance.objects.filter(emp=e, date=DAY)
        self.assertEqual(rows.count(), 1)
        rec = rows.get()
        self.assertTrue(rec.status != "L" or (rec.check_in is None and rec.check_out is None))  # constraint held
