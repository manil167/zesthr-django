"""Work-schedule policies: which schedule applies, and what a day's punches mean under it.

THE POLICIES (also written up in docs/ATTENDANCE_POLICY.md)

Which schedule applies     the employee's own schedule, else their department's, else the company
                           default (the schedule flagged "default"). No schedule at all: no rules apply.
Lateness                   arriving more than `grace_minutes` after the start counts as late, and the
                           late time is measured from the start (not from the end of the grace period).
Early departure            leaving more than `early_leave_grace_minutes` before the end counts, measured
                           from the end time.
Worked time                check-out minus check-in, less the schedule's unpaid break when the stay was
                           longer than the break.
Overtime                   only for schedules with overtime_eligible. Time after the end time counts once
                           it reaches `overtime_after_minutes`, then in full, up to `overtime_max_minutes`
                           per day (0 = no limit). Arriving early earns nothing.
Rest days                  working on a day that is not one of the schedule's working days counts all
                           worked time as overtime (at most 12 hours), if eligible.
Overnight shifts           when the end time is earlier than the start, the shift runs into the next day;
                           the record belongs to the day it STARTED, and the check-out is flagged
                           `ends_next_day`. Durations always come out positive.
Only Present days count    absent / leave / half-day records are not evaluated.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..models import WorkSchedule

REST_DAY_MAX_MINUTES = 12 * 60


@dataclass(frozen=True)
class DayResult:
    working_day: bool
    late_minutes: int = 0
    early_minutes: int = 0
    overtime_minutes: int = 0
    worked_minutes: int | None = None

    @property
    def late_text(self):
        return minutes_text(self.late_minutes)

    @property
    def early_text(self):
        return minutes_text(self.early_minutes)

    @property
    def overtime_text(self):
        return minutes_text(self.overtime_minutes)


def minutes_text(n):
    """90 -> '1 h 30 m'; 45 -> '45 m'; 0 -> ''."""
    if not n:
        return ""
    hours, mins = divmod(int(n), 60)
    return f"{hours} h {mins:02d} m" if hours else f"{mins} m"


class ScheduleResolver:
    """Resolves schedules cheaply for many employees (loads the company default once)."""

    def __init__(self):
        self._defaults = {}

    def for_employee(self, emp):
        if emp.work_schedule_id:
            return emp.work_schedule
        if emp.department_id and emp.department.work_schedule_id:
            return emp.department.work_schedule
        if emp.organization_id not in self._defaults:
            self._defaults[emp.organization_id] = WorkSchedule.objects.filter(
                organization_id=emp.organization_id, is_default=True, is_active=True).first()
        return self._defaults[emp.organization_id]


def resolve_schedule(emp):
    return ScheduleResolver().for_employee(emp)


def _minutes(delta):
    return int(delta.total_seconds() // 60)


def shift_bounds(schedule, day):
    """(start, end) datetimes of the shift that begins on `day`."""
    start = datetime.combine(day, schedule.start_time)
    end = datetime.combine(day + timedelta(days=1 if schedule.is_overnight else 0), schedule.end_time)
    return start, end


def scheduled_minutes(schedule):
    """Paid length of a normal shift in minutes (break excluded)."""
    start, end = shift_bounds(schedule, datetime(2000, 1, 3).date())
    return max(0, _minutes(end - start) - schedule.break_minutes)


def evaluate(rec, schedule):
    """What the punches of one attendance record mean under `schedule` (None if no schedule/not Present)."""
    if schedule is None or rec.status != "P":
        return None
    start, end = shift_bounds(schedule, rec.date)
    cin = datetime.combine(rec.date, rec.check_in) if rec.check_in else None
    cout = (datetime.combine(rec.date + timedelta(days=1 if rec.ends_next_day else 0), rec.check_out)
            if rec.check_out else None)
    worked = None
    if cin and cout:
        raw = _minutes(cout - cin)
        worked = max(0, raw - (schedule.break_minutes if raw > schedule.break_minutes else 0))
    working_day = rec.date.weekday() in schedule.working_days
    late = early = overtime = 0
    if working_day:
        if cin and _minutes(cin - start) > schedule.grace_minutes:
            late = _minutes(cin - start)
        if cout and _minutes(end - cout) > schedule.early_leave_grace_minutes:
            early = _minutes(end - cout)
        if cout and schedule.overtime_eligible:
            extra = _minutes(cout - end)
            if extra > 0 and extra >= schedule.overtime_after_minutes:
                overtime = min(extra, schedule.overtime_max_minutes) if schedule.overtime_max_minutes else extra
    elif worked and schedule.overtime_eligible:
        overtime = min(worked, REST_DAY_MAX_MINUTES)
    return DayResult(working_day, late, early, overtime, worked)
