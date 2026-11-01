# Business policies and data ownership

## Access and ownership

An organization owns employee records. Attendance, leave, payroll, documents, and employment history inherit their employee's organization. Memberships restrict tenant access; superusers are platform-wide administrators and should be reserved for tightly controlled administration. Role names are convenience groups, while permission checks and tenant/object scope determine access. See core/roles.py for the definitive permission mapping.

Employees see their own self-service records. Managers review the direct-report scope supported by the relevant service. Payroll access requires compensation permissions. Account operators cannot grant themselves privileges or edit protected privileged accounts. Application users cannot delete protected history through normal administration. Database administrators remain technically capable of changing data; protect database credentials and audit access separately.

User accounts are separate from employee records. Onboarding currently uses restricted account provisioning followed by employee linking and assignment. Deactivation is transactional with linked-login revocation and preserves attendance, leave, payroll, and employment records. Future-dated changes are applied explicitly with `apply_scheduled_employment_changes`.

## Attendance

One attendance record per employee and date is enforced by the database. Check-in/out services handle lifecycle and duplicate-punch rules. Corrections require explanations and authorized review; changes preserve audit entries. Missing check-outs, repeated lateness, duplicate submissions, long shifts, and insufficient explanations create reviewable exceptions. They must not be represented as automatic misconduct findings.

Schedule precedence is employee, department, then company default. Overnight shifts belong to their starting date. Grace periods, unpaid breaks, overtime eligibility, thresholds, and caps are documented in ATTENDANCE_POLICY.md. Location time zones are supported; the legacy default remains configurable through DJANGO_TIME_ZONE. Existing global holidays remain relevant to leave/exception calculations. Per-location holiday calendars are not fully integrated and must not be advertised as complete.

## Leave

Submission validates employment dates, active status, date ranges, overlaps, and entitlement. Pending applications reserve entitlement. Decisions lock the employee/request rows, preserve an immutable decision, and update attendance transactionally. Reviewers cannot approve their own requests. Cancellation restores the audited prior attendance only when no intervening change makes restoration unsafe.

Configured annual entitlements support carryover, explicit balance adjustments, and monthly accrual. Monthly accrual posts at the start of each eligible month and rounds down to hundredths. An entitlement can count working days using the assigned schedule and existing global holiday table. Without configured entitlement, the established sick-leave fallback remains one day per calendar month; calendar days otherwise preserve the established policy. Configure and review actual company policies before production use.

## Payroll

Amounts use Decimal and cent rounding with ROUND_HALF_UP. Per-day basic pay equals monthly basic divided by calendar days, rounded to cents. Absent equivalent days equal absent days plus half-day records divided by two. Absence deduction is capped at basic salary. Approved leave does not create an absence deduction. Unmarked days are review exceptions; they must not silently be asserted to be verified attendance.

Overtime uses eligible schedule evaluation: overtime minutes times per-day pay times multiplier divided by paid minutes in a normal shift. Recurring component amounts are prorated over their effective calendar-day overlap with the period. Overlapping assignments are rejected. Approved one-off adjustments contribute earnings or deductions. Net pay is gross less approved deductions; deductions exceeding available gross are rejected.

Review stores calculation details and component amounts in a snapshot. Finalization requires the supported independent approval workflow. Historical finalized payslips retain the snapshot rather than recalculating from current employee information. Reversal preserves the original payroll and audit trail. Country-specific tax, statutory contributions, banking files, and accounting certification require separately validated policies and integrations.

## Documents and retention

Only authorized users can upload or access employment PDFs. Validation rejects oversized, invalid, or active-content PDFs before storage. Downloads require application authorization and create audit events. Retention dates prevent early purge; an authorized request can later be processed by the retry-safe purge command. Storage administrators must separately manage prior S3 versions and backups. See OPERATIONS.md; application purge alone is not a guarantee of deletion across every retained copy.
