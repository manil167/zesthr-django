from datetime import date, time, timedelta

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from core.models import Attendance, AttendanceAudit, Employee, EmploymentHistory, SecurityEvent
from core.services import attendance as att
from core.services import audit as audit_service

from .test_permissions import BaseTest

TODAY = timezone.localdate()
D1 = TODAY - timedelta(days=3)


class HistoryBase(BaseTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Employee.objects.filter(pk__in=[cls.alice.pk, cls.bob.pk]).update(joined_on=date(2024, 1, 1))
        cls.alice.refresh_from_db()
        cls.bob.refresh_from_db()
        Attendance.objects.all().delete()

    def get(self, user=None, **params):
        self.login(user or self.hr_admin)
        return self.client.get(reverse("attendance_history"), params)


class DescribeTests(HistoryBase):
    def entry(self, **kw):
        return AttendanceAudit.objects.create(emp=self.alice, date=D1, **kw)

    def test_created_lists_only_what_was_set(self):
        e = self.entry(action="MANUAL_CREATE", old_values=None,
                       new_values={"status": "P", "check_in": "09:05:00", "check_out": None, "ends_next_day": False})
        self.assertEqual(audit_service.describe(e), [("Status", "—", "Present"), ("Check-in", "—", "09:05")])

    def test_edit_lists_only_what_changed(self):
        e = self.entry(action="MANUAL_EDIT",
                       old_values={"status": "P", "check_in": "09:00:00", "check_out": "17:00:00", "ends_next_day": False},
                       new_values={"status": "P", "check_in": "09:00:00", "check_out": "18:30:00", "ends_next_day": False})
        self.assertEqual(audit_service.describe(e), [("Check-out", "17:00", "18:30")])

    def test_status_change_that_clears_times(self):
        e = self.entry(action="SET_STATUS",
                       old_values={"status": "P", "check_in": "09:00:00", "check_out": None, "ends_next_day": False},
                       new_values={"status": "L", "check_in": None, "check_out": None, "ends_next_day": False})
        self.assertEqual(audit_service.describe(e), [("Status", "Present", "Leave"), ("Check-in", "09:00", "—")])

    def test_delete_shows_what_was_lost(self):
        e = self.entry(action="MANUAL_DELETE", new_values=None,
                       old_values={"status": "P", "check_in": "09:00:00", "check_out": "17:00:00", "ends_next_day": False})
        rows = audit_service.describe(e)
        self.assertEqual(rows[0], ("Status", "Present", "deleted"))
        self.assertIn(("Check-out", "17:00", "—"), rows)

    def test_overnight_flag_is_shown_when_it_changes(self):
        e = self.entry(action="CHECK_OUT",
                       old_values={"status": "P", "check_in": "22:00:00", "check_out": None, "ends_next_day": False},
                       new_values={"status": "P", "check_in": "22:00:00", "check_out": "06:00:00", "ends_next_day": True})
        self.assertEqual(audit_service.describe(e), [("Check-out", "—", "06:00"), ("Check-out next day", "no", "yes")])


class HistoryAccessTests(HistoryBase):
    def test_who_may_open_it(self):
        for user, code in ((self.hr_admin, 200), (self.superuser, 200), (self.hr_officer, 403), (self.payroll_officer, 403),
                           (self.sysadmin, 403), (self.alice_user, 403), (self.nobody, 403)):
            self.assertEqual(self.get(user).status_code, code, user.username)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("attendance_history")).status_code, 302)

    def test_it_is_read_only(self):
        self.login(self.hr_admin)
        r = self.client.get(reverse("attendance_history"))
        self.assertNotContains(r, 'action="/attendance/')  # nothing on the page posts a change
        self.assertEqual(self.client.post(reverse("attendance_history"), {"reason": "x"}).status_code, 200)  # POST just re-renders

    def test_nav_and_row_links_only_for_those_with_access(self):
        att.check_in(self.alice, now=time(9), today=D1, by=self.hr_admin)
        self.login(self.hr_admin)
        self.assertContains(self.client.get(reverse("attendance_list"), {"from": D1.isoformat(), "to": D1.isoformat()}), "attendance/history/?emp=")
        self.assertContains(self.client.get(reverse("attendance_register")), "Change history")
        self.login(self.hr_officer)
        r = self.client.get(reverse("attendance_list"), {"from": D1.isoformat(), "to": D1.isoformat()})
        self.assertNotContains(r, "attendance/history/")
        self.assertNotContains(r, "Change history")


class HistoryContentTests(HistoryBase):
    def setUp(self):
        att.check_in(self.alice, now=time(9), today=D1, by=self.alice_user)
        att.check_out(self.alice, now=time(17), today=D1, by=self.alice_user)
        att.save_manual(self.alice, D1, status="P", check_in=time(9), check_out=time(18, 30), by=self.hr_officer, reason="stayed late")
        att.save_manual(self.bob, D1, status="L", by=self.hr_officer, reason="approved leave")

    def test_entries_show_who_when_what_and_why(self):
        r = self.get(emp=self.alice.pk)
        self.assertContains(r, "stayed late")
        self.assertContains(r, "17:00 &rarr; <b>18:30</b>")
        self.assertContains(r, "hrofficer")
        self.assertContains(r, "alice <span class=\"muted\">(the employee)</span>")
        self.assertEqual(len(r.context["page"]), 3)

    def test_newest_first(self):
        entries = list(self.get().context["page"])
        self.assertEqual([e.created_at for e in entries], sorted((e.created_at for e in entries), reverse=True))

    def test_filters(self):
        self.assertEqual(self.get(emp=self.bob.pk).context["total"], 1)
        self.assertEqual(self.get(action="CHECK_OUT").context["total"], 1)
        self.assertEqual(self.get(by="hrofficer").context["total"], 2)
        self.assertEqual(self.get(by="ALICE").context["total"], 2)       # case-insensitive
        self.assertEqual(self.get(q="leave").context["total"], 1)
        self.assertEqual(self.get(**{"from": D1.isoformat(), "to": D1.isoformat()}).context["total"], 4)
        self.assertEqual(self.get(**{"from": (D1 + timedelta(days=1)).isoformat()}).context["total"], 0)
        self.assertEqual(self.get(changed_from=TODAY.isoformat(), changed_to=TODAY.isoformat()).context["total"], 4)
        self.assertEqual(self.get(changed_to=(TODAY - timedelta(days=1)).isoformat()).context["total"], 0)

    def test_filters_combine(self):
        self.assertEqual(self.get(emp=self.alice.pk, by="hrofficer").context["total"], 1)

    def test_bad_filter_values_are_ignored(self):
        r = self.get(emp="abc", action="<script>", **{"from": "garbage"}, changed_to="2026-13-45", page="zzz")
        self.assertEqual((r.status_code, r.context["total"]), (200, 4))  # every filter ignored -> all four entries

    def test_a_correction_request_is_referenced(self):
        from core.services import corrections
        req = corrections.submit(self.bob, date=D1 - timedelta(days=1), status="A", reason="was off", by=self.bob_user)
        corrections.decide(req, approve=True, by=self.hr_admin)
        self.assertContains(self.get(emp=self.bob.pk), f"Correction request #{req.pk}")

    def test_deleting_a_record_leaves_its_history_readable(self):
        rec = Attendance.objects.get(emp=self.bob, date=D1)
        self.login(self.hr_admin)
        self.client.post(reverse("attendance_delete", args=[rec.pk]), {"reason": "entered by mistake"})
        self.assertFalse(Attendance.objects.filter(pk=rec.pk).exists())
        r = self.client.get(reverse("attendance_history"), {"emp": self.bob.pk})
        self.assertContains(r, "Entry deleted")
        self.assertContains(r, "entered by mistake")
        self.assertContains(r, "Leave &rarr; <b>deleted</b>")  # what the record said before it was deleted
        self.assertEqual(AttendanceAudit.objects.filter(emp=self.bob).count(), 2)

    def test_pagination_keeps_filters(self):
        for i in range(60):
            att.save_manual(self.bob, D1 - timedelta(days=i + 1), status="A", by=self.hr_officer, reason="bulk fill")
        r = self.get(by="hrofficer")
        self.assertEqual(len(r.context["page"]), 50)
        self.assertContains(r, "by=hrofficer")
        self.assertContains(r, "page=2")
        self.assertEqual(self.get(by="hrofficer", page=2).context["page"].number, 2)

    def test_system_entries_without_a_user_are_labelled(self):
        AttendanceAudit.objects.create(emp=self.bob, date=D1, action="SET_STATUS", old_values=None,
                                       new_values={"status": "A", "check_in": None, "check_out": None, "ends_next_day": False})
        self.assertContains(self.get(emp=self.bob.pk), ">system<")


class AppendOnlyOrmTests(HistoryBase):
    def make(self):
        return att.check_in(self.alice, now=time(9), today=D1, by=self.hr_admin)

    def test_attendance_audit_cannot_be_changed_or_deleted_through_the_orm(self):
        self.make()
        row = AttendanceAudit.objects.get()
        row.reason = "tampered"
        for action in (row.save, row.delete, lambda: AttendanceAudit.objects.all().delete(),
                       lambda: AttendanceAudit.objects.update(reason="x"),
                       lambda: AttendanceAudit.objects.bulk_update([row], ["reason"])):
            with self.assertRaises(ValidationError):
                action()
        self.assertEqual(AttendanceAudit.objects.get().reason, "")

    def test_the_other_history_tables_are_protected_the_same_way(self):
        ev = SecurityEvent.objects.create(event_type="LOGOUT", username="x")
        hist = EmploymentHistory.objects.create(employee=self.alice, field="created", new_value="x")
        for obj in (ev, hist):
            with self.assertRaises(ValidationError):
                obj.delete()
            obj.pk and setattr(obj, "_state", obj._state)
            with self.assertRaises(ValidationError):
                obj.save()
        with self.assertRaises(ValidationError):
            SecurityEvent.objects.all().delete()
        with self.assertRaises(ValidationError):
            EmploymentHistory.objects.update(field="x")

    def test_inserting_is_still_fine(self):
        self.make()
        AttendanceAudit.objects.bulk_create([AttendanceAudit(emp=self.alice, date=D1, action="SET_STATUS")])
        self.assertEqual(AttendanceAudit.objects.count(), 2)


class AppendOnlyDatabaseTests(HistoryBase):
    """Enforced by database triggers, so it holds even for raw SQL from the application's own account."""

    def setUp(self):
        att.check_in(self.alice, now=time(9), today=D1, by=self.hr_admin)
        self.row = AttendanceAudit.objects.get()

    def raw(self, sql, params=()):
        with connection.cursor() as cur:
            cur.execute(sql, params)

    def refuses(self, sql, params=()):
        # match the trigger's own message, so a type error or typo could never pass for "refused"
        with self.assertRaisesRegex(DatabaseError, r"attendance_audit rows cannot be (modified|deleted)"), transaction.atomic():
            self.raw(sql, params)

    def test_content_cannot_be_updated_with_raw_sql(self):
        for column, value in (("reason", "tampered"), ("action", "SET_STATUS"), ("date", "2020-01-01"),
                              ("emp_id", self.bob.pk), ("record_id", 999)):
            self.refuses(f"UPDATE attendance_audit SET {column} = %s WHERE id = %s", [value, self.row.pk])
        self.refuses("UPDATE attendance_audit SET created_at = created_at + interval '1 day'" if connection.vendor == "postgresql"
                     else "UPDATE attendance_audit SET created_at = '2001-01-01 00:00:00'")
        self.refuses("UPDATE attendance_audit SET old_values = '{\"status\": \"A\"}'")
        self.refuses("UPDATE attendance_audit SET new_values = '{\"status\": \"A\"}'")
        self.row.refresh_from_db()
        self.assertEqual((self.row.reason, self.row.action), ("", "CHECK_IN"))

    def test_rows_cannot_be_deleted_with_raw_sql(self):
        self.refuses("DELETE FROM attendance_audit WHERE id = %s", [self.row.pk])
        self.refuses("DELETE FROM attendance_audit")
        self.assertEqual(AttendanceAudit.objects.count(), 1)

    def test_links_can_still_be_cleared_when_the_things_they_point_to_go_away(self):
        # deleting the attendance record (HR "delete entry") nulls the link, the history stays
        rec = Attendance.objects.get(emp=self.alice, date=D1)
        rec_id = rec.pk  # Django clears pk on a deleted instance
        rec.delete()
        self.row.refresh_from_db()
        self.assertIsNone(self.row.attendance_id)
        self.assertEqual((self.row.record_id, self.row.date), (rec_id, D1))  # copied, so it stays traceable
        # deleting the user who made the change nulls changed_by, the history stays
        self.hr_admin.delete()
        self.row.refresh_from_db()
        self.assertIsNone(self.row.changed_by_id)
        self.assertEqual(AttendanceAudit.objects.count(), 1)

    def test_only_link_columns_may_be_updated_by_raw_sql(self):
        self.raw("UPDATE attendance_audit SET changed_by_id = NULL, attendance_id = NULL, correction_request_id = NULL")
        self.row.refresh_from_db()
        self.assertEqual((self.row.changed_by_id, self.row.attendance_id), (None, None))

    def test_new_rows_can_always_be_added(self):
        self.raw("INSERT INTO attendance_audit (emp_id, date, action, reason, created_at) VALUES (%s, %s, 'SET_STATUS', 'raw insert', %s)",
                 [self.alice.pk, D1.isoformat(), timezone.now().isoformat()])
        self.assertEqual(AttendanceAudit.objects.count(), 2)
