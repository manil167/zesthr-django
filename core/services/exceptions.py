"""Attendance exception finder: days that need attention, so HR can fix them.

Kinds
    MISSING_CHECKOUT  punched in, never punched out (a day that has already ended)
    NO_TIMES          marked Present but with no punch times at all
    UNMARKED          a working day with no attendance record for an employee who should have one
    LATE / EARLY      arrived late / left early under the employee's schedule
    OVERTIME          overtime earned (useful to review before payroll)

Nothing is stored: every row is worked out from the records, so once HR fixes a day it simply
disappears from the list. "Working day" uses the employee's schedule, or Monday-Friday when there is none;
company holidays and days outside the employee's employment dates are skipped.
"""
from collections import namedtuple
from datetime import timedelta

from apps.organizations.tenancy import scope_queryset
from ..models import Attendance, Employee, Holiday
from .. import timeutil
from . import schedules

MISSING_CHECKOUT, NO_TIMES, UNMARKED = "missing_checkout", "no_times", "unmarked"
LATE, EARLY, OVERTIME = "late", "early", "overtime"
KINDS = [(MISSING_CHECKOUT, "Missing check-out"), (NO_TIMES, "Present without times"), (UNMARKED, "Unmarked working day"),
         (LATE, "Late arrival"), (EARLY, "Early departure"), (OVERTIME, "Overtime")]
DEFAULT_KINDS = (MISSING_CHECKOUT, NO_TIMES, UNMARKED, LATE, EARLY)
MISSING_PUNCH_KINDS = (MISSING_CHECKOUT, NO_TIMES, UNMARKED)
MAX_DAYS = 62

LABELS = dict(KINDS)


class Row(namedtuple("Row", "kind emp date record detail")):
    @property
    def label(self):
        return LABELS[self.kind]


def _employed_on(emp, day):
    if emp.joined_on > day:
        return False
    if emp.separation_date and day > emp.separation_date:
        return False
    return emp.is_active or bool(emp.separation_date and day <= emp.separation_date)


def find_exceptions(date_from, date_to, *, kinds=DEFAULT_KINDS, department_id=None, emp_id=None, user=None):
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise ValueError(f"Choose a range of at most {MAX_DAYS} days.")
    kinds = set(kinds)
    resolver = schedules.ScheduleResolver()
    people = Employee.objects.select_related("work_schedule", "department__work_schedule", "work_location")
    if user is not None:
        people = scope_queryset(people, user)
    if department_id:
        people = people.filter(department_id=department_id)
    if emp_id:
        people = people.filter(pk=emp_id)
    people = list(people)
    by_id = {e.pk: e for e in people}
    records = Attendance.objects.filter(emp_id__in=by_id, date__range=(date_from, date_to))
    rows, have = [], set()
    for rec in records:
        emp = by_id[rec.emp_id]
        have.add((rec.emp_id, rec.date))
        schedule = resolver.for_employee(emp)
        past = rec.date < timeutil.today_for(emp)
        if rec.status in ("P", "H") and rec.check_in and not rec.check_out and past and MISSING_CHECKOUT in kinds:
            rows.append(Row(MISSING_CHECKOUT, emp, rec.date, rec, f"Punched in at {rec.check_in:%H:%M}, no check-out"))
        if rec.status == "P" and not rec.check_in and not rec.check_out and NO_TIMES in kinds:
            rows.append(Row(NO_TIMES, emp, rec.date, rec, "Marked present with no punch times"))
        result = schedules.evaluate(rec, schedule)
        if result:
            if result.late_minutes and LATE in kinds:
                rows.append(Row(LATE, emp, rec.date, rec, f"{result.late_text} late"))
            if result.early_minutes and EARLY in kinds:
                rows.append(Row(EARLY, emp, rec.date, rec, f"Left {result.early_text} early"))
            if result.overtime_minutes and OVERTIME in kinds:
                rows.append(Row(OVERTIME, emp, rec.date, rec, f"{result.overtime_text} overtime"))
    if UNMARKED in kinds:
        holidays = set(Holiday.objects.filter(date__range=(date_from, date_to)).values_list("organization_id", "date"))
        for emp in people:
            schedule = resolver.for_employee(emp)
            working_days = schedule.working_days if schedule else [0, 1, 2, 3, 4]
            today = timeutil.today_for(emp)
            day = date_from
            while day <= date_to:
                if (day < today and (emp.organization_id, day) not in holidays and day.weekday() in working_days
                        and (emp.pk, day) not in have and _employed_on(emp, day)):
                    rows.append(Row(UNMARKED, emp, day, None, "No attendance recorded"))
                day += timedelta(days=1)
    rows.sort(key=lambda r: (r.date, r.emp.name, r.kind))
    return rows
