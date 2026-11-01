"""Turns an AttendanceAudit row into something a person can read."""
from ..models import Attendance, AttendanceAudit

STATUS = dict(Attendance.STATUS)
FIELDS = [("status", "Status"), ("check_in", "Check-in"), ("check_out", "Check-out"),
          ("ends_next_day", "Check-out next day")]


def _fmt(field, value):
    if value is None:
        return "—"
    if field == "status":
        return STATUS.get(value, value)
    if field in ("check_in", "check_out"):
        return value[:5]  # "09:05:00" -> "09:05"
    if field == "ends_next_day":
        return "yes" if value else "no"
    return str(value)


def describe(audit):
    """[(label, old, new)] for the fields this entry is about.

    created -> every value that was set; deleted -> every value that was lost;
    otherwise only the fields that actually changed.
    """
    old, new = audit.old_values, audit.new_values
    deleted = audit.action == AttendanceAudit.MANUAL_DELETE
    rows = []
    for field, label in FIELDS:
        before = old.get(field) if old else None
        after = None if deleted else (new.get(field) if new else None)
        if old and new and before == after:
            continue
        if field == "ends_next_day" and not before and not after:
            continue  # "no" -> nothing: not worth a line
        if old is None and after in (None, False):
            continue  # a new record: only list what was actually set
        rows.append((label, _fmt(field, before) if old else "—", "deleted" if deleted and field == "status" else _fmt(field, after)))
    return rows
