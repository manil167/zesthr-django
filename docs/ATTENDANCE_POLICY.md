# Attendance, schedules and overtime: the rules

These rules are implemented in `core/services/schedules.py` (what punches mean),
`core/services/attendance.py` (punching) and `core/services/payroll.py` (pay). Each rule has tests.

## Which schedule applies to an employee

1. the employee's own **work schedule**, else
2. the **department's** schedule, else
3. the company **default** schedule (the one flagged *default*; only one is allowed), else
4. **none**: no lateness, early-leave or overtime rules are applied, and payroll is exactly as without schedules.

Schedules are managed in `/admin/` (Work schedules). Fields: start and end time, working days
(0 = Monday ... 6 = Sunday), unpaid break, late grace, early-leave grace, overtime settings.

## Daily rules (only for days with status *Present*)

| Rule | Policy |
|---|---|
| **Late** | Arriving more than the *late grace* minutes after the start. The late time is measured **from the start time** (09:25 for a 09:00 start with 10 min grace is 25 minutes late). Arriving inside the grace is on time. |
| **Early departure** | Leaving more than the *early-leave grace* before the end time, measured from the end time. |
| **Worked time** | Check-out minus check-in, minus the unpaid break when the stay was longer than the break. |
| **Rest days** | On a day that is not one of the schedule's working days there is no lateness or early departure. |
| **Not evaluated** | Absent, Leave and Half-day records; days with no check-out have no early/overtime result. |

## Overtime (paid)

Only for schedules with *overtime eligible* switched on.

* **Normal days:** time after the end time counts once it reaches *overtime after* minutes (default 30),
  then **in full**, up to *overtime maximum* minutes per day (default 240; 0 = no limit). The maximum protects
  against paying for a forgotten punch-out. Arriving early earns nothing.
* **Rest days:** all worked time (break excluded) counts as overtime, at most 12 hours.
* **Pay:** `hourly rate = per-day pay / paid hours of a normal shift`, and
  `overtime pay = overtime hours x hourly rate x multiplier` (default 1.50).
  Per-day pay is the basic salary divided by the calendar days of the month (the existing payroll policy).
* Payslip: `gross = basic + overtime`, `net = gross - deductions`.
* **No overtime means no change.** An employee who earned none (no schedule, not eligible, below the
  threshold, never punched out, ...) gets exactly the same figures as before overtime existed.
* Example: basic 31,000 in a 31-day month, 09:00-18:00 with a 60 minute break (8 paid hours), 90 minutes
  of overtime at x1.5 -> per-day 1,000, hourly 125, overtime pay `1.5 h x 125 x 1.5 = 281.25`.

## Overnight shifts

If the end time is earlier than the start time (e.g. 22:00 to 06:00) the shift runs into the next day.

* The record belongs to the day the shift **started**.
* The employee punches in in the evening and out the next morning; that check-out is attached to the
  open record and flagged *next day*. Durations are always positive (22:00 to 06:00 = 8 hours).
* HR corrections tick *Check-out is on the next day*. A shift of 24 hours or more is refused.
* Lateness, early departure and overtime are measured against the shift across midnight.

## Time zones

The company time zone is the `DJANGO_TIME_ZONE` setting (default `Asia/Kolkata`; set it to
`Africa/Lagos` for Nigeria). A **work location** may have its own time zone; employees at that location
get their own "today" and clock time when punching, and the "no future dates" rule uses it too.

## Registers, bulk entry and exceptions

* **Daily register** (any date): everyone's record for the day, with counts. People with no record are
  *Unmarked* only on a working day; rest days and company holidays show *Not a working day*.
* **Bulk marking:** tick employees and mark them Present / Absent / Half day / Leave with one reason. Each person
  succeeds or fails on its own; people who already have a record are skipped unless "replace existing" is chosen
  (which needs the change-attendance permission). Every change is written to the audit history.
* **Exceptions and missing punches:** computed live, nothing stored, so fixing a day removes it from the list.
  Kinds: missing check-out (past days only), present without times, unmarked working day, late, early, overtime.
  Employees without a schedule are treated as Monday-Friday workers. Range limit: 62 days.

## Correction requests

1. An employee requests a fix for a day (their own, up to `DJANGO_CORRECTION_WINDOW_DAYS` back, default 30, never
   in the future) with the values it *should* have and a reason. The values must pass the normal attendance
   rules and differ from the current record. One open request per day (enforced by the database).
2. **Who decides:** HR roles (permission *review attendance corrections*) for anyone, and an employee's **direct
   manager** for their own team. Nobody decides their own request, HR included.
3. **Approve:** applies the change to the locked record and writes an audit entry linked to the request.
   **Reject:** needs a note, changes nothing. A decision is final; simultaneous clicks apply exactly once.
4. The employee is notified on their dashboard; reviewers see a count in the sidebar and on the dashboard.

## Change history (audit)

* **What is recorded:** every punch, HR status change, manual entry, correction (including approved correction
  requests, linked back to the request) and deletion: the old values, the new values, who did it, when and why.
* **Who can read it:** users with *view attendance audit* (HR Administrator, superusers) at
  **Attendance > Change history**, filterable by employee, attendance day, change date, type, user and reason.
* **Nobody can change it.** In the application the history tables only accept inserts. For attendance
  history the database itself also refuses: any UPDATE of a row's content or any DELETE (even raw SQL) raises an
  error. The only UPDATEs allowed are the links being cleared when the record or user they point to is deleted;
  the employee, day, values, reason and time always stay. If a retention policy ever requires purging old
  entries, that must be a deliberate migration that drops the triggers (see `0014_attendance_audit_triggers`).
* **Deleted records stay traceable:** the history keeps the record id and day, and what the record said.

## Known limits

* Overtime is not approved by a manager before it is paid; the daily maximum and threshold are the guard rails.
* Public holidays are not yet treated as rest days (Commit 7.3 adds work calendars).
* Shifts are one start/end pair per day; split shifts are not supported.
