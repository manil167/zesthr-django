from core.permissions import get_employee_profile
"""Attendance correction requests: an employee asks, a manager or HR decides.

Who may decide
    * users holding `review_attendance_corrections` (HR roles): any employee's request, and
    * the requester's direct manager (the employee linked to the user is `requester.manager`).
    Nobody may decide their own request.

Rules for a request
    * only for yourself, only while active, only for a date within CORRECTION_WINDOW_DAYS and not in the future
    * one open request per day; the requested values must pass the normal attendance rules and actually
      differ from the current record
    * a reason is mandatory

Approving applies the change through attendance.save_manual (locked row, audit entry linked to the
request); rejecting needs a note and changes no attendance. A decided request can never be decided again.
"""
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.organizations.tenancy import scope_queryset
from ..models import Employee
from .. import roles, timeutil
from ..models import Attendance, AttendanceCorrectionRequest as Request
from . import attendance as att
from .attendance import AttendanceError


def window_start(emp):
    return timeutil.today_for(emp) - timedelta(days=settings.CORRECTION_WINDOW_DAYS)


def can_review(user, req):
    """May `user` approve or reject `req`?"""
    if not user.is_authenticated or not scope_queryset(Employee.objects.filter(pk=req.emp_id), user).exists() or req.emp.user_id == user.pk:
        return False  # never your own request
    if user.has_perm(roles.REVIEW_CORRECTIONS):
        return scope_queryset(Employee.objects.filter(pk=req.emp_id), user).exists()
    profile = get_employee_profile(user)
    return profile is not None and profile.is_active and req.emp.manager_id == profile.pk


def reviewable_requests(user):
    """Queryset of the requests this user may decide (any status)."""
    qs = scope_queryset(Request.objects.select_related("emp", "decided_by"), user, "emp__organization")
    if user.has_perm(roles.REVIEW_CORRECTIONS):
        return qs.exclude(emp__user=user)
    profile = get_employee_profile(user)
    if profile is None or not profile.is_active:
        return qs.none()
    return qs.filter(emp__manager=profile).exclude(emp__user=user)


@transaction.atomic
def submit(emp, *, date, status, check_in=None, check_out=None, ends_next_day=False, reason, by=None):
    if not emp.is_active:
        raise AttendanceError("inactive_employee", "Your employee record is inactive, so requests cannot be made.")
    if not (reason or "").strip():
        raise AttendanceError("reason_required", "Please explain what needs correcting.")
    today = timeutil.today_for(emp)
    if date > today:
        raise AttendanceError("future_date", "You cannot request a correction for a future date.")
    if date < window_start(emp):
        raise AttendanceError("too_old", f"Corrections can only be requested for the last "
                                         f"{settings.CORRECTION_WINDOW_DAYS} days. Please contact HR.")
    wanted = att.build_candidate(emp, date, status=status, check_in=check_in, check_out=check_out,
                                 ends_next_day=ends_next_day)
    att.validate_candidate(wanted)
    current = Attendance.objects.filter(emp=emp, date=date).first()
    if current is not None and att.snapshot(current) == att.snapshot(wanted):
        raise AttendanceError("no_change", "That is already what the record says.")
    try:
        with transaction.atomic():
            return Request.objects.create(
                emp=emp, date=date, requested_status=wanted.status, requested_check_in=wanted.check_in,
                requested_check_out=wanted.check_out, requested_ends_next_day=wanted.ends_next_day,
                reason=reason.strip()[:255], original_values=att.snapshot(current), requested_by=by)
    except IntegrityError:
        raise AttendanceError("already_pending", f"You already have an open request for {date:%d %b %Y}.") from None


@transaction.atomic
def cancel(req, *, by):
    req = Request.objects.select_for_update().get(pk=req.pk)
    if req.emp.user_id != getattr(by, "pk", None):
        raise AttendanceError("not_yours", "You can only cancel your own requests.")
    if req.status != Request.PENDING:
        raise AttendanceError("already_decided", "This request has already been decided.")
    req.status, req.decided_at = Request.CANCELLED, timezone.now()
    req.save(update_fields=["status", "decided_at"])
    return req


@transaction.atomic
def decide(req, *, approve, note="", by):
    """Approve or reject. Raises AttendanceError for anything that should not go through."""
    req = Request.objects.select_for_update().select_related("emp").get(pk=req.pk)
    if not can_review(by, req):
        raise AttendanceError("not_allowed", "You are not allowed to decide this request.")
    if req.status != Request.PENDING:
        raise AttendanceError("already_decided", f"This request was already {req.get_status_display().lower()}.")
    note = (note or "").strip()
    if not approve and not note:
        raise AttendanceError("note_required", "Please say why the request is rejected.")
    if approve:
        att.save_manual(req.emp, req.date, status=req.requested_status, check_in=req.requested_check_in,
                        check_out=req.requested_check_out, ends_next_day=req.requested_ends_next_day, by=by,
                        reason=f"Correction request #{req.pk}: {req.reason}", correction=req)
    req.status = Request.APPROVED if approve else Request.REJECTED
    req.decided_by, req.decided_at, req.decision_note = by, timezone.now(), note[:255]
    req.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return req
