"""Attendance rules: punching in/out, status changes by HR, and the audit trail.

Allowed transitions for ONE employee on ONE day (the row is locked while it changes, so
two simultaneous requests can never both succeed):

    no row        --check_in-->   Present, check_in set
    Present(no in)--check_in-->   Present, check_in set      (HR created an empty Present entry)
    Present(in)   --check_out-->  Present, check_out set     (must be later than check_in)
    anything else on check_in / check_out is refused:
        already punched in/out, not punched in, absent/leave/half-day set by HR, inactive employee

    HR status changes (Absent / Half day / Leave):
    no row or Present(no punches) --> A / H / L              allowed
    A <-> L <-> H without punches                            allowed
    row with punches --> A / L   would erase real punch times: refused unless a reason is given
    same status again                                        no-op

Every change writes an AttendanceAudit row in the same transaction.
Corrections of any other kind go through the edit form, which requires a reason.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction

from .. import timeutil
from ..models import Attendance, AttendanceAudit, Employee
from .schedules import resolve_schedule

MARKABLE_STATUSES = ("A", "H", "L")  # statuses HR may set explicitly


class AttendanceError(Exception):
    """A rule was broken. `code` is stable for tests/UI; str() is a message fit for the user."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def snapshot(rec):
    """The fields that matter, as plain JSON-safe values."""
    if rec is None:
        return None
    return {"status": rec.status,
            "check_in": rec.check_in.isoformat() if rec.check_in else None,
            "check_out": rec.check_out.isoformat() if rec.check_out else None,
            "ends_next_day": rec.ends_next_day}


def record_audit(action, rec, before, *, by=None, reason="", day=None, emp=None, deleted=False, correction=None):
    return AttendanceAudit.objects.create(correction_request=correction, 
        emp=emp or rec.emp, date=day or rec.date, attendance=None if deleted else rec, record_id=rec.pk,
        action=action, old_values=before, new_values=None if deleted else snapshot(rec),
        reason=(reason or "").strip()[:255], changed_by=by if getattr(by, "pk", None) else None)


def _require_active(emp):
    emp = Employee.objects.select_for_update().get(pk=emp.pk)
    if not emp.is_active:
        raise AttendanceError("inactive_employee", f"{emp.name} is not an active employee, so attendance cannot be recorded.")


@transaction.atomic
def check_in(emp, *, now=None, today=None, by=None):
    _require_active(emp)
    day, moment = today or timeutil.today_for(emp), now or timeutil.time_for(emp)
    rec, created = Attendance.objects.select_for_update().get_or_create(
        emp=emp, date=day, defaults={"status": "P", "check_in": moment})
    if created:
        record_audit(AttendanceAudit.CHECK_IN, rec, None, by=by)
        return rec
    if rec.check_in:
        raise AttendanceError("already_checked_in", f"Already punched in today at {rec.check_in:%H:%M}.")
    if rec.status == "L":
        raise AttendanceError("status_locked", "Today is recorded as approved leave, so punching in is not possible.")
    if rec.status in ("A", "H"):
        raise AttendanceError("status_locked", f"HR has marked today as {rec.get_status_display().lower()}. "
                                               "Ask HR to correct it if that is wrong.")
    before = snapshot(rec)  # an empty Present entry that HR created
    rec.check_in, rec.status = moment, "P"
    rec.save()
    record_audit(AttendanceAudit.CHECK_IN, rec, before, by=by)
    return rec


def current_record(emp, today):
    """The record a punch card should show: today's, or the open shift that started yesterday
    when the employee works overnight shifts."""
    rec = Attendance.objects.filter(emp=emp, date=today).first()
    if rec and rec.check_in and not rec.check_out:
        return rec
    schedule = resolve_schedule(emp)
    if schedule and schedule.is_overnight:
        prev = Attendance.objects.filter(emp=emp, date=today - timedelta(days=1), check_in__isnull=False,
                                         check_out__isnull=True, status__in=["P", "H"]).first()
        if prev:
            return prev
    return rec


@transaction.atomic
def check_out(emp, *, now=None, today=None, by=None):
    _require_active(emp)
    day, moment = today or timeutil.today_for(emp), now or timeutil.time_for(emp)
    rec = Attendance.objects.select_for_update().filter(emp=emp, date=day).first()
    open_today = rec is not None and rec.check_in is not None and rec.check_out is None

    if not open_today:
        # Overnight shift: the employee clocked in yesterday evening and leaves this morning.
        schedule = resolve_schedule(emp)
        if schedule and schedule.is_overnight:
            prev = Attendance.objects.select_for_update().filter(
                emp=emp, date=day - timedelta(days=1), check_in__isnull=False, check_out__isnull=True,
                status__in=["P", "H"]).first()
            if prev:
                if moment >= prev.check_in:
                    raise AttendanceError("invalid_time", "That shift would be 24 hours or longer. Ask HR to correct it.")
                before = snapshot(prev)
                prev.check_out, prev.ends_next_day = moment, True
                prev.save()
                record_audit(AttendanceAudit.CHECK_OUT, prev, before, by=by)
                return prev
        if rec is not None and rec.check_out:
            raise AttendanceError("already_checked_out", f"Already punched out today at {rec.check_out:%H:%M}.")
        hint = ""
        if Attendance.objects.filter(emp=emp, date__lt=day, check_in__isnull=False, check_out__isnull=True,
                                     status__in=["P", "H"]).exists():
            hint = " You also have an earlier day without a check-out; ask HR to correct it."
        raise AttendanceError("not_checked_in", "You have not punched in today, so there is nothing to punch out of." + hint)

    if moment <= rec.check_in:
        raise AttendanceError("invalid_time", "The check-out time must be later than the check-in time "
                                              f"({rec.check_in:%H:%M}). Check the device clock.")
    before = snapshot(rec)
    rec.check_out = moment
    rec.save()
    record_audit(AttendanceAudit.CHECK_OUT, rec, before, by=by)
    return rec


@transaction.atomic
def mark_status(emp, status, *, today=None, by=None, reason=""):
    """HR sets today's status to Absent / Half day / Leave."""
    if status not in MARKABLE_STATUSES:
        raise ValueError(f"Unsupported attendance status: {status!r}")
    day = today or timeutil.today_for(emp)
    rec, created = Attendance.objects.select_for_update().get_or_create(emp=emp, date=day, defaults={"status": status})
    if created:
        record_audit(AttendanceAudit.SET_STATUS, rec, None, by=by, reason=reason)
        return rec
    if rec.status == status:
        return rec  # nothing to change: repeating the click is harmless
    erases_punches = status in ("A", "L") and (rec.check_in or rec.check_out)
    if erases_punches and not (reason or "").strip():
        raise AttendanceError(
            "correction_required",
            f"{emp.name} already has punch times today. Use Edit attendance and give a reason to override them.")
    before = snapshot(rec)
    rec.status = status
    if status in ("A", "L"):
        rec.check_in = rec.check_out = None
        rec.ends_next_day = False
    rec.save()
    record_audit(AttendanceAudit.SET_STATUS, rec, before, by=by, reason=reason)
    return rec


def record_manual_change(action, rec, before, *, by, reason, deleted=False, day=None, emp=None):
    """For edits made through the correction form (the form validates; this keeps the history)."""
    if action == AttendanceAudit.MANUAL_EDIT and before == snapshot(rec) and not deleted:
        return None  # saved without changing anything: nothing to record
    return record_audit(action, rec, before, by=by, reason=reason, deleted=deleted, day=day, emp=emp)


# --- manual entry (HR registers, bulk marking, approved corrections) -----------------------
MAX_BULK = 500


def build_candidate(emp, day, *, status, check_in=None, check_out=None, ends_next_day=False):
    """An unsaved Attendance with the wanted values, normalised (Absent/Leave carry no times)."""
    if status in ("A", "L"):
        check_in = check_out = None
        ends_next_day = False
    return Attendance(emp=emp, date=day, status=status, check_in=check_in, check_out=check_out,
                      ends_next_day=bool(ends_next_day and check_in and check_out))


def validate_candidate(rec):
    """Run the single-row rules; raises AttendanceError with every problem in one message."""
    try:
        rec.clean()
    except ValidationError as exc:
        raise AttendanceError("invalid", " ".join(m for msgs in exc.message_dict.values() for m in msgs)) from exc


@transaction.atomic
def save_manual(emp, day, *, status, check_in=None, check_out=None, ends_next_day=False, by, reason,
                overwrite=True, correction=None):
    """Create or change one day's record on behalf of HR / an approved correction.

    Returns (record, outcome) with outcome 'created', 'updated' or 'unchanged'. A reason is mandatory.
    The row is locked while it changes and every real change is written to the audit history.
    """
    if not (reason or "").strip():
        raise AttendanceError("reason_required", "A reason is required for manual attendance changes.")
    Employee.objects.select_for_update().get(pk=emp.pk)
    wanted = build_candidate(emp, day, status=status, check_in=check_in, check_out=check_out, ends_next_day=ends_next_day)
    validate_candidate(wanted)
    rec, created = Attendance.objects.select_for_update().get_or_create(
        emp=emp, date=day, defaults={"status": wanted.status, "check_in": wanted.check_in,
                                     "check_out": wanted.check_out, "ends_next_day": wanted.ends_next_day})
    if created:
        record_audit(AttendanceAudit.MANUAL_CREATE, rec, None, by=by, reason=reason, correction=correction)
        return rec, "created"
    if not overwrite:
        raise AttendanceError("exists", f"{emp.name} already has a record for {day:%d %b %Y}.")
    before = snapshot(rec)
    rec.status, rec.check_in, rec.check_out, rec.ends_next_day = (
        wanted.status, wanted.check_in, wanted.check_out, wanted.ends_next_day)
    if snapshot(rec) == before:
        return rec, "unchanged"
    rec.save()
    record_audit(AttendanceAudit.MANUAL_EDIT, rec, before, by=by, reason=reason, correction=correction)
    return rec, "updated"


@dataclass(frozen=True)
class BulkResult:
    emp: object
    outcome: str  # created / updated / unchanged / skipped
    message: str = ""


def bulk_mark(employees, day, *, status, check_in=None, check_out=None, ends_next_day=False, by, reason,
              overwrite=False):
    """Apply the same status/times to many employees for one day.

    Each employee succeeds or fails on its own (a savepoint per person), so one bad row never blocks the
    rest. Existing records are left alone unless `overwrite` is set.
    """
    employees = list(employees)
    if len(employees) > MAX_BULK:
        raise AttendanceError("too_many", f"At most {MAX_BULK} employees can be changed at once.")
    if not (reason or "").strip():
        raise AttendanceError("reason_required", "A reason is required for manual attendance changes.")
    results = []
    for emp in employees:
        try:
            with transaction.atomic():
                _, outcome = save_manual(emp, day, status=status, check_in=check_in, check_out=check_out,
                                         ends_next_day=ends_next_day, by=by, reason=reason, overwrite=overwrite)
            results.append(BulkResult(emp, outcome))
        except AttendanceError as exc:
            results.append(BulkResult(emp, "skipped", str(exc)))
    return results
