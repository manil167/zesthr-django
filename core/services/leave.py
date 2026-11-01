from core.permissions import get_employee_profile
"""Transactional leave submission, review, and cancellation.

Calendar days count toward leave, preserving the established policy. Pending
requests reserve quota; conflicting requests and existing punches need review.
"""
from datetime import date, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.organizations.tenancy import scope_queryset
from .. import roles
from ..models import Attendance, AttendanceAudit, Employee, LeaveApplication, LeaveDecision
from . import attendance as attendance_service
from . import notifications, leave_balances
from ..models import LeaveEntitlement

SICK_LEAVE_MAX_DAYS = 1
SICK_LEAVE_MONTHLY_QUOTA = 1


def validate_leave_request(employee, leave_type, start, end, *, exclude_pk=None):
    if not (start and end):
        return
    if end < start:
        raise ValidationError("End date cannot be earlier than start date.")
    if (end - start).days > 365:
        raise ValidationError("A leave request may cover at most 366 days.")
    if leave_type not in dict(LeaveApplication.LEAVE_TYPES):
        raise ValidationError("Choose a valid leave type.")
    configured = employee is not None and LeaveEntitlement.objects.filter(
        employee=employee, leave_type=leave_type, year=start.year).exists()
    if leave_type == "SL" and not configured and (end - start).days + 1 > SICK_LEAVE_MAX_DAYS:
        raise ValidationError("Sick leave allowance is strictly 1 day per month.")
    if employee is None:
        return
    if start < employee.joined_on or (employee.separation_date and end > employee.separation_date):
        raise ValidationError("Leave dates must fall within employment dates.")
    applications = LeaveApplication.objects.filter(
        emp=employee, status__in=["PENDING", "APPROVED"]).exclude(pk=exclude_pk)
    if applications.filter(start_date__lte=end, end_date__gte=start).exists():
        raise ValidationError("This request overlaps pending or approved leave.")
    leave_balances.validate_entitlements(employee, leave_type, start, end, exclude_pk=exclude_pk)
    if leave_type == "SL" and not configured:
        used = applications.filter(leave_type="SL", start_date__year=start.year,
                                   start_date__month=start.month).count()
        if used >= SICK_LEAVE_MONTHLY_QUOTA:
            raise ValidationError(f"You have exhausted your Sick Leave allocation for {start:%B %Y}.")


def _authorize(leave, by):
    if by is None:  # internal management/service caller
        return
    profile = get_employee_profile(by)
    manager = bool(profile and profile.is_active and leave.emp.manager_id == profile.pk)
    if not by.is_authenticated or not (by.has_perm(roles.APPROVE_LEAVE) or manager):
        raise PermissionDenied
    if leave.emp.user_id == by.pk:
        raise PermissionDenied("You cannot decide your own leave request.")
    if not scope_queryset(Employee.objects.filter(pk=leave.emp_id), by).exists():
        raise PermissionDenied


def _locked(leave):
    # All requests for one employee use the same parent-row lock, including new
    # requests which do not yet have a row to lock.
    Employee.objects.select_for_update().get(pk=leave.emp_id)
    return LeaveApplication.objects.select_for_update().select_related("emp").get(pk=leave.pk)


@transaction.atomic
def submit_leave(employee, *, leave_type, start_date, end_date, reason):
    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    if not employee.is_active:
        raise ValidationError("Inactive employees cannot submit leave.")
    if not (reason or "").strip():
        raise ValidationError("Explain the reason for your leave request.")
    validate_leave_request(employee, leave_type, start_date, end_date)
    leave = LeaveApplication.objects.create(
        emp=employee, leave_type=leave_type, start_date=start_date, end_date=end_date,
        reason=reason.strip(), status="PENDING")
    notifications.notify_pending_leave(leave)
    return leave


@transaction.atomic
def approve_leave(leave, note="", *, by=None):
    leave = _locked(leave)
    _authorize(leave, by)
    if leave.status == "APPROVED":
        return leave
    if leave.status != "PENDING":
        raise ValidationError("Only pending leave can be approved.")
    validate_leave_request(leave.emp, leave.leave_type, leave.start_date, leave.end_date,
                           exclude_pk=leave.pk)
    records = Attendance.objects.select_for_update().filter(
        emp=leave.emp, date__range=(leave.start_date, leave.end_date))
    if records.filter(check_in__isnull=False).exists() or records.filter(check_out__isnull=False).exists():
        raise ValidationError("Recorded punches conflict with this leave. Review attendance first.")
    before = {rec.date.isoformat(): attendance_service.snapshot(rec) for rec in records}
    day = leave.start_date
    while day <= leave.end_date:
        attendance_service.save_manual(leave.emp, day, status="L", by=by,
                                       reason=f"Approved leave request #{leave.pk}")
        day += timedelta(days=1)
    leave.status, leave.admin_note = "APPROVED", note.strip()[:255]
    leave.save(update_fields=["status", "admin_note"])
    LeaveDecision.objects.create(leave=leave, status=leave.status, decided_by=by,
                                 note=leave.admin_note, previous_attendance=before)
    notifications.notify_leave_decision(leave)
    return leave


@transaction.atomic
def reject_leave(leave, note="", *, by=None):
    leave = _locked(leave)
    _authorize(leave, by)
    if leave.status == "REJECTED":
        return leave
    if leave.status != "PENDING":
        raise ValidationError("Only pending leave can be rejected. Cancel approved leave to reverse it.")
    leave.status, leave.admin_note = "REJECTED", note.strip()[:255]
    leave.save(update_fields=["status", "admin_note"])
    LeaveDecision.objects.create(leave=leave, status=leave.status, decided_by=by, note=leave.admin_note)
    notifications.notify_leave_decision(leave)
    return leave


@transaction.atomic
def cancel_leave(leave, *, by, note):
    leave = _locked(leave)
    if not note.strip():
        raise ValidationError("Explain the cancellation.")
    owner = leave.emp.user_id == by.pk
    if not (owner and leave.status == "PENDING"):
        _authorize(leave, by)
    if leave.status not in ("PENDING", "APPROVED"):
        raise ValidationError("This leave request cannot be cancelled.")
    if leave.status == "APPROVED":
        decision = leave.decisions.get(status="APPROVED")
        day = leave.start_date
        while day <= leave.end_date:
            record = Attendance.objects.select_for_update().filter(emp=leave.emp, date=day).first()
            if (not record or record.status != "L" or record.check_in or record.check_out or
                    AttendanceAudit.objects.filter(emp=leave.emp, date=day,
                                                   created_at__gt=decision.created_at).exists()):
                raise ValidationError("Attendance changed after approval. Review it before cancellation.")
            previous = decision.previous_attendance.get(day.isoformat())
            if previous is None:
                attendance_service.record_audit(AttendanceAudit.MANUAL_DELETE, record,
                    attendance_service.snapshot(record), by=by, reason=note, deleted=True, emp=leave.emp, day=day)
                record.delete()
            else:
                attendance_service.save_manual(leave.emp, day, status=previous["status"],
                    check_in=time.fromisoformat(previous["check_in"]) if previous["check_in"] else None,
                    check_out=time.fromisoformat(previous["check_out"]) if previous["check_out"] else None,
                    ends_next_day=previous.get("ends_next_day", False), by=by, reason=note)
            day += timedelta(days=1)
    leave.status, leave.admin_note = "CANCELLED", note.strip()[:255]
    leave.save(update_fields=["status", "admin_note"])
    LeaveDecision.objects.create(leave=leave, status=leave.status, decided_by=by, note=leave.admin_note)
    notifications.notify_leave_decision(leave)
    return leave


def reviewable_leaves(user):
    queryset = scope_queryset(LeaveApplication.objects.select_related("emp", "emp__department"),
                              user, "emp__organization")
    if user.has_perm(roles.APPROVE_LEAVE):
        return queryset.exclude(emp__user=user)
    profile = get_employee_profile(user)
    return (queryset.filter(emp__manager=profile).exclude(emp__user=user)
            if profile and profile.is_active else queryset.none())
