"""Payroll calculation.

    net = basic - deduction + overtime_pay

Per-day pay is the basic salary divided by the calendar days of the month; a half
day counts as 0.5 absent day; the deduction never exceeds the basic salary.

Overtime pay (employees whose schedule is overtime-eligible only):

    hourly rate  = per-day pay / paid hours of a normal shift
    overtime pay = overtime hours x hourly rate x the schedule's multiplier

An employee with no overtime gets no overtime term at all, so their figures are exactly
what they were before overtime existed.
"""

"""Payroll calculation and payroll business rules."""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Prefetch
from apps.organizations.tenancy import scope_queryset

from .. import roles
from ..models import Attendance, Employee, Salary, PayrollEvent, EmployeePayrollComponent, PayrollAdjustment
from . import schedules

from django.utils import timezone

from ..models import (
    PayrollAdjustment,
    PayrollRun,
)

CENT = Decimal("0.01")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PayrollCalculation:
    employee: Employee
    month: int
    year: int
    calendar_days: int
    present_days: int
    half_days: int
    absent_days_count: int
    leave_days: int
    absent_equivalent_days: Decimal
    basic_salary: Decimal
    per_day_pay: Decimal
    absence_deduction: Decimal
    overtime_minutes: int
    overtime_pay: Decimal
    gross_pay: Decimal
    net_pay: Decimal
    additional_earnings: Decimal = Decimal("0")
    additional_deductions: Decimal = Decimal("0")
    components: tuple = ()

    @property
    def total_deduction(self):
        return self.absence_deduction + self.additional_deductions

    def __getitem__(self, key):
        """Compatibility with the report's established dictionary-style row API."""
        fields = {
            "emp": self.employee,
            "present": self.present_days,
            "half": self.half_days,
            "absent": self.absent_days_count,
            "leave": self.leave_days,
            "absent_days": self.absent_equivalent_days,
            "per_day": self.per_day_pay,
            "deduction": self.total_deduction,
            "overtime_minutes": self.overtime_minutes,
            "overtime_pay": self.overtime_pay,
            "gross": self.gross_pay,
            "net": self.net_pay,
        }
        try:
            return fields[key]
        except KeyError:
            raise KeyError(key) from None


def validate_period(month, year):
    if not 1 <= int(month) <= 12:
        raise ValidationError("Month must be between 1 and 12.")

    if int(year) < 2000 or int(year) > 2100:
        raise ValidationError("Invalid payroll year.")


def get_attendance_totals(employee, month, year):
    if hasattr(employee, "_period_attendance"):
        return {key: sum(record.status == status for record in employee._period_attendance)
                for key, status in (("present", "P"), ("half", "H"), ("absent", "A"), ("leave", "L"))}
    result = employee.attendance.filter(
        date__month=month,
        date__year=year,
    ).aggregate(
        present=Count(
            "id",
            filter=Q(status="P"),
        ),
        half=Count(
            "id",
            filter=Q(status="H"),
        ),
        absent=Count(
            "id",
            filter=Q(status="A"),
        ),
        leave=Count(
            "id",
            filter=Q(status="L"),
        ),
    )

    return {
        "present": result["present"] or 0,
        "half": result["half"] or 0,
        "absent": result["absent"] or 0,
        "leave": result["leave"] or 0,
    }


def calculate_overtime(employee, month, year, per_day_pay):
    resolver = schedules.ScheduleResolver()

    records = (
        Attendance.objects
        .filter(
            emp=employee,
            date__month=month,
            date__year=year,
            status="P",
            check_out__isnull=False,
        )
        .select_related(
            "emp__work_schedule",
            "emp__department__work_schedule",
        )
    )

    total_minutes = 0
    multiplier = Decimal("1.00")
    scheduled_minutes = 0

    if hasattr(employee, "_period_attendance"):
        records = [record for record in employee._period_attendance if record.status == "P" and record.check_out]
    for record in records:
        schedule = getattr(employee, "_payroll_schedule", None) or resolver.for_employee(employee)

        if not schedule or not schedule.overtime_eligible:
            continue

        evaluation = schedules.evaluate(record, schedule)

        if not evaluation:
            continue

        if evaluation.overtime_minutes <= 0:
            continue

        total_minutes += evaluation.overtime_minutes
        multiplier = schedule.overtime_multiplier
        scheduled_minutes = schedules.scheduled_minutes(schedule)

    if not total_minutes or not scheduled_minutes:
        return 0, Decimal("0.00")

    overtime_pay = (
        Decimal(total_minutes)
        * per_day_pay
        * multiplier
        / Decimal(scheduled_minutes)
    )

    return total_minutes, money(overtime_pay)


def calculate_employee_payroll(employee, month, year):
    validate_period(month, year)

    calendar_days = calendar.monthrange(year, month)[1]

    totals = get_attendance_totals(
        employee,
        month,
        year,
    )

    basic_salary = money(employee.basic_salary)

    per_day_pay = money(
        basic_salary / Decimal(calendar_days)
    )

    absent_equivalent_days = (
        Decimal(totals["absent"])
        + Decimal(totals["half"]) / Decimal("2")
    )

    absence_deduction = money(
        min(
            absent_equivalent_days * per_day_pay,
            basic_salary,
        )
    )

    overtime_minutes, overtime_pay = calculate_overtime(
        employee,
        month,
        year,
        per_day_pay,
    )

    first, last = date(year, month, 1), date(year, month, calendar_days)
    components = []
    assignment_ranges = {}
    assignments = (employee._payroll_components if hasattr(employee, "_payroll_components") else
                   employee.payroll_components.select_related("component").filter(active=True, component__active=True))
    for assignment in assignments:
        if assignment.component.organization_id != employee.organization_id:
            raise ValidationError("Payroll component belongs to another organization.")
        start = max(first, assignment.effective_from)
        end = min(last, assignment.effective_to or last)
        if start <= end:
            previous = assignment_ranges.setdefault(assignment.component_id, [])
            if any(start <= old_end and end >= old_start for old_start, old_end in previous):
                raise ValidationError(f"Overlapping payroll component assignments for {employee.name}.")
            previous.append((start, end))
            amount = money(assignment.amount * Decimal((end - start).days + 1) / calendar_days)
            components.append((assignment.component.name, assignment.component.component_type, str(amount)))
    adjustments = (employee._payroll_adjustments if hasattr(employee, "_payroll_adjustments") else
                   employee.payroll_adjustments.filter(month=month, year=year, approved=True))
    for adjustment in adjustments:
        components.append((adjustment.description, adjustment.component_type, str(money(adjustment.amount))))
    earnings = sum((Decimal(amount) for _, kind, amount in components if kind == "EARNING"), Decimal(0))
    deductions = sum((Decimal(amount) for _, kind, amount in components if kind == "DEDUCTION"), Decimal(0))
    gross_pay = money(basic_salary + overtime_pay + earnings)
    if absence_deduction + deductions > gross_pay:
        raise ValidationError(f"Deductions exceed gross pay for {employee.name}; review the adjustments.")
    net_pay = money(gross_pay - absence_deduction - deductions)

    return PayrollCalculation(
        employee=employee,
        month=month,
        year=year,
        calendar_days=calendar_days,
        present_days=totals["present"],
        half_days=totals["half"],
        absent_days_count=totals["absent"],
        leave_days=totals["leave"],
        absent_equivalent_days=absent_equivalent_days,
        basic_salary=basic_salary,
        per_day_pay=per_day_pay,
        absence_deduction=absence_deduction,
        overtime_minutes=overtime_minutes,
        overtime_pay=overtime_pay,
        gross_pay=gross_pay,
        net_pay=net_pay, additional_earnings=earnings, additional_deductions=deductions,
        components=tuple(components),
    )


def calculate_payroll_rows(month, year, *, user=None, organization=None):
    validate_period(month, year)

    employees = (
        Employee.objects
        .filter(is_active=True)
        .select_related(
            "department",
            "position",
            "work_schedule", "department__work_schedule", "organization",
        )
        .prefetch_related(
            Prefetch("attendance", queryset=Attendance.objects.filter(date__month=month, date__year=year), to_attr="_period_attendance"),
            Prefetch("payroll_components", queryset=EmployeePayrollComponent.objects.filter(active=True, component__active=True).select_related("component"), to_attr="_payroll_components"),
            Prefetch("payroll_adjustments", queryset=PayrollAdjustment.objects.filter(month=month, year=year, approved=True), to_attr="_payroll_adjustments"),
        )
        .order_by("name")
    )

    if user is not None:
        employees = scope_queryset(employees, user)
    if organization is not None:
        employees = employees.filter(organization=organization)
    resolver = schedules.ScheduleResolver()
    employees = list(employees)
    for employee in employees:
        employee._payroll_schedule = resolver.for_employee(employee)
    return [
        calculate_employee_payroll(
            employee,
            month,
            year,
        )
        for employee in employees
    ]


def calculation_snapshot(row):
    return {"employee_id": row.employee.pk, "name": row.employee.name,
            "designation": row.employee.designation, "currency": row.employee.organization.default_currency,
            "basic": str(row.basic_salary), "gross": str(row.gross_pay), "net": str(row.net_pay),
            "deduction": str(row.total_deduction), "absence_deduction": str(row.absence_deduction),
            "absent_days": str(row.absent_equivalent_days), "per_day_pay": str(row.per_day_pay),
            "overtime_minutes": row.overtime_minutes, "overtime_pay": str(row.overtime_pay),
            "components": row.components, "month": row.month, "year": row.year,
            "counts": {"present": row.present_days, "half": row.half_days,
                       "absent": row.absent_days_count, "leave": row.leave_days}}


def _persist_snapshot(items):
    for item in items:
        employee = Employee.objects.get(pk=item["employee_id"])
        defaults = {field: item[field] for field in ("gross", "net", "deduction", "absent_days",
                                                     "per_day_pay", "overtime_minutes", "overtime_pay")}
        defaults["calculation_snapshot"] = item
        Salary.objects.update_or_create(emp=employee, month=item["month"], year=item["year"], defaults=defaults)


@transaction.atomic
def generate_payroll(month, year, *, user=None):
    rows = calculate_payroll_rows(month, year, user=user)
    organization_ids = sorted({row.employee.organization_id for row in rows})
    for organization_id in organization_ids:
        run, _ = PayrollRun.objects.select_for_update().get_or_create(
            organization_id=organization_id, month=month, year=year, defaults={"created_by": user})
        if run.status != PayrollRun.DRAFT:
            raise ValidationError("Only draft payroll can be recalculated.")
    _persist_snapshot([calculation_snapshot(row) for row in rows])
    return len(rows)


def attendance_totals(salary):
    if salary.calculation_snapshot:
        return salary.calculation_snapshot["counts"]
    result = salary.emp.attendance.filter(
        date__month=salary.month,
        date__year=salary.year,
    ).aggregate(
        present=Count(
            "id",
            filter=Q(status="P"),
        ),
        half=Count(
            "id",
            filter=Q(status="H"),
        ),
        absent=Count(
            "id",
            filter=Q(status="A"),
        ),
        leave=Count(
            "id",
            filter=Q(status="L"),
        ),
    )

    return result

def _authorize_run(run, user, permission):
    if user is None:
        return
    if not user.has_perm(permission) or not scope_queryset(
            PayrollRun.objects.filter(pk=run.pk), user).exists():
        raise PermissionDenied


def _event(run, action, user=None, reason=""):
    PayrollEvent.objects.create(run=run, action=action, actor=user, reason=reason[:255])


@transaction.atomic
def create_payroll_run(month, year, user, *, organization=None):
    from apps.organizations.models import Organization
    validate_period(month, year)
    organizations = scope_queryset(Organization.objects.all(), user, "pk")
    if organization is None:
        choices = list(organizations[:2])
        if len(choices) != 1:
            raise ValidationError("Select one organization.")
        organization = choices[0]
    if not organizations.filter(pk=organization.pk).exists() or not user.has_perm(roles.GENERATE_PAYROLL):
        raise PermissionDenied
    run, created = PayrollRun.objects.select_for_update().get_or_create(
        organization=organization, month=month, year=year,
        defaults={"created_by": user, "policy_version": "2.0"})
    if run.is_locked:
        raise ValidationError("A finalized or reversed payroll cannot be regenerated.")
    if created:
        _event(run, "CREATED", user)
    return run


@transaction.atomic
def submit_payroll_for_approval(run, user=None):
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    _authorize_run(run, user, roles.GENERATE_PAYROLL)
    if run.status != PayrollRun.DRAFT:
        raise ValidationError("Only draft payroll runs can be submitted.")
    rows = calculate_payroll_rows(run.month, run.year, organization=run.organization)
    if not rows:
        raise ValidationError("There are no active employees to process.")
    run.calculation_snapshot = [calculation_snapshot(row) for row in rows]
    run.policy_version = "2.0"
    run.status = PayrollRun.PENDING_APPROVAL
    run.save(update_fields=["calculation_snapshot", "policy_version", "status"])
    _event(run, "SUBMITTED", user)
    return run


@transaction.atomic
def approve_payroll(run, user):
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    _authorize_run(run, user, "core.approve_payroll")
    if run.created_by_id == user.pk:
        raise ValidationError("A different authorized reviewer must approve this payroll.")
    if run.status != PayrollRun.PENDING_APPROVAL:
        raise ValidationError("Only payroll awaiting approval can be approved.")
    run.status, run.approved_by, run.approved_at = PayrollRun.APPROVED, user, timezone.now()
    run.save(update_fields=["status", "approved_by", "approved_at"])
    _event(run, "APPROVED", user)
    return run


@transaction.atomic
def finalize_payroll(run, user):
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    _authorize_run(run, user, "core.finalize_payroll")
    if run.status != PayrollRun.APPROVED or not run.calculation_snapshot:
        raise ValidationError("Only approved payroll with a calculation snapshot can be finalized.")
    _persist_snapshot(run.calculation_snapshot)
    run.status, run.finalized_by, run.finalized_at = PayrollRun.FINALIZED, user, timezone.now()
    run.save(update_fields=["status", "finalized_by", "finalized_at"])
    _event(run, "FINALIZED", user)
    return run


@transaction.atomic
def reverse_payroll(run, *, user, reason):
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    _authorize_run(run, user, "core.reverse_payroll")
    if run.status != PayrollRun.FINALIZED or not reason.strip():
        raise ValidationError("Only finalized payroll can be reversed, with an explanation.")
    run.status = PayrollRun.REVERSED
    run.save(update_fields=["status"])
    _event(run, "REVERSED", user, reason)
    return run
