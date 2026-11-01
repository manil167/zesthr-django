"""Employee lifecycle: safe deactivation and reactivation.

An employee is never deleted to "remove" them: attendance, leave and payroll
history must survive. Deactivation keeps every record, marks the employee as
no longer active, and revokes the linked login in the same transaction.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied

from apps.organizations.tenancy import scope_queryset

from ..models import Employee, ScheduledEmploymentChange
from . import accounts, history

DEACTIVATION_STATUSES = ("SUSPENDED", "INACTIVE", "RESIGNED", "TERMINATED", "RETIRED")


@transaction.atomic
def deactivate_employee(employee, *, status, separation_date=None, reason="", by=None):
    if status not in DEACTIVATION_STATUSES:
        raise ValidationError("Choose a valid deactivation status.")
    if status in Employee.SEPARATED_STATUSES and not separation_date:
        raise ValidationError("A separation date is required for this status.")
    before = history.refresh_and_snapshot(employee.pk)
    employee.employment_status = status
    employee.separation_date = separation_date if status in Employee.SEPARATED_STATUSES else None
    employee.separation_reason = reason.strip()
    employee.full_clean()
    employee.save()
    history.record_changes(before, employee, by)
    if employee.user_id:  # revoke access immediately
        accounts.set_account_active(employee.user, False, by=by)
    return employee


@transaction.atomic
def reactivate_employee(employee, *, status="ACTIVE", by=None):
    if status not in Employee.ACTIVE_STATUSES:
        raise ValidationError("Reactivate as Active or On probation.")
    before = history.refresh_and_snapshot(employee.pk)
    employee.employment_status = status
    employee.separation_date = None
    employee.separation_reason = ""
    employee.save()
    history.record_changes(before, employee, by)
    if employee.user_id:
        accounts.set_account_active(employee.user, True, by=by)
    return employee


@transaction.atomic
def apply_scheduled_change(change, *, by=None):
    """Apply one due future change and preserve an auditable before/after record."""
    change = ScheduledEmploymentChange.objects.select_for_update().select_related("employee").get(pk=change.pk)
    if change.applied_at:
        raise ValidationError("This scheduled employment change has already been applied.")
    if change.effective_date > timezone.localdate():
        raise ValidationError("This change is not effective yet.")
    if by is not None:
        from core import roles
        if not by.has_perm(roles.CHANGE_EMPLOYEE) or not scope_queryset(
            Employee.objects.filter(pk=change.employee_id), by
        ).exists():
            raise PermissionDenied
    change.full_clean()
    employee = Employee.objects.select_for_update().get(pk=change.employee_id)
    before = history.snapshot(employee)
    if change.designation:
        employee.designation = change.designation
    if change.department_id:
        employee.department_id = change.department_id
    if change.manager_id:
        employee.manager_id = change.manager_id
    employee.full_clean()
    employee.save()
    history.record_changes(before, employee, by)
    change.applied_at = timezone.now()
    change.save(update_fields=["applied_at"])
    return employee
