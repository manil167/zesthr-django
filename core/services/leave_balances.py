"""Explainable annual balances; monthly accrual posts at the start of a month.

Pending and approved applications reserve entitlement. Without an entitlement,
the legacy policy remains in force; no arbitrary annual allowance is invented.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.organizations.tenancy import scope_queryset
from .. import roles
from ..models import Employee, Holiday, LeaveApplication, LeaveEntitlement, LeaveBalanceAdjustment
from .schedules import resolve_schedule


@dataclass(frozen=True)
class Balance:
    entitlement: object
    earned: Decimal
    carryover: Decimal
    adjustments: Decimal
    approved: Decimal
    pending: Decimal
    remaining: Decimal


def chargeable_days(employee, start, end, entitlement):
    if end < start:
        return Decimal(0)
    if not entitlement.working_days_only:
        return Decimal((end - start).days + 1)
    schedule = resolve_schedule(employee)
    weekdays = schedule.working_days if schedule else [0, 1, 2, 3, 4]
    holidays = set(Holiday.objects.filter(organization_id=employee.organization_id, date__range=(start, end)).values_list("date", flat=True))
    day, total = start, 0
    while day <= end:
        if day.weekday() in weekdays and day not in holidays:
            total += 1
        day += timedelta(days=1)
    return Decimal(total)


def balance(entitlement, *, as_of=None, exclude_pk=None):
    as_of = as_of or timezone.localdate()
    employee, year = entitlement.employee, entitlement.year
    earned = entitlement.annual_days
    if entitlement.monthly_accrual:
        start_month = employee.joined_on.month if employee.joined_on.year == year else 1
        months = max(0, min(as_of.month if as_of.year == year else (12 if as_of.year > year else 0),
                            12) - start_month + 1)
        if employee.joined_on.year > year:
            months = 0
        earned = (entitlement.annual_days * Decimal(months) / 12).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    adjustments = entitlement.adjustments.aggregate(total=Sum("days"))["total"] or Decimal(0)
    counts = {"PENDING": Decimal(0), "APPROVED": Decimal(0)}
    first, last = date(year, 1, 1), date(year, 12, 31)
    for request in LeaveApplication.objects.filter(
            emp=employee, leave_type=entitlement.leave_type, status__in=counts,
            start_date__lte=last, end_date__gte=first).exclude(pk=exclude_pk):
        counts[request.status] += chargeable_days(employee, max(first, request.start_date),
                                                  min(last, request.end_date), entitlement)
    available = earned + entitlement.carryover_days + adjustments - counts["APPROVED"] - counts["PENDING"]
    return Balance(entitlement, earned, entitlement.carryover_days, adjustments,
                   counts["APPROVED"], counts["PENDING"], available)


def validate_entitlements(employee, leave_type, start, end, *, exclude_pk=None):
    for year in range(start.year, end.year + 1):
        entitlement = LeaveEntitlement.objects.filter(employee=employee, leave_type=leave_type, year=year).first()
        if entitlement is None:
            continue
        first, last = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        requested = chargeable_days(employee, first, last, entitlement)
        if requested > balance(entitlement, exclude_pk=exclude_pk).remaining:
            raise ValidationError(f"Insufficient {leave_type} leave balance for {year}.")
        if entitlement.monthly_limit is not None:
            month_start = first.replace(day=1)
            while month_start <= last:
                next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
                month_end = next_month - timedelta(days=1)
                reserved = Decimal(0)
                for request in LeaveApplication.objects.filter(
                        emp=employee, leave_type=leave_type, status__in=["PENDING", "APPROVED"],
                        start_date__lte=month_end, end_date__gte=month_start).exclude(pk=exclude_pk):
                    reserved += chargeable_days(employee, max(request.start_date, month_start),
                                                min(request.end_date, month_end), entitlement)
                added = chargeable_days(employee, max(first, month_start), min(last, month_end), entitlement)
                if reserved + added > entitlement.monthly_limit:
                    raise ValidationError(f"Monthly leave limit exceeded for {month_start:%B %Y}.")
                month_start = next_month


@transaction.atomic
def adjust_balance(entitlement, *, days, reason, by):
    employee = Employee.objects.select_for_update().get(pk=entitlement.employee_id)
    if not by.has_perm(roles.CHANGE_EMPLOYEE) or not scope_queryset(Employee.objects.filter(pk=employee.pk), by).exists():
        raise PermissionDenied
    adjustment = LeaveBalanceAdjustment(entitlement=entitlement, days=days, reason=reason.strip(), changed_by=by)
    adjustment.full_clean()
    adjustment.save()
    return adjustment
