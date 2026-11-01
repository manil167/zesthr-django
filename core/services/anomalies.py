"""Reviewable attendance signals. A signal is never a finding of misconduct."""
from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.functions import Length
from apps.organizations.tenancy import scope_queryset
from .. import roles, timeutil
from ..models import Attendance, AttendanceAudit, AttendanceCorrectionRequest, Employee, AttendanceAnomaly, AnomalyDecision
from .schedules import ScheduleResolver, evaluate


def record(employee, day, kind, evidence, *, source=""):
    fingerprint = sha256(f"{employee.pk}:{day}:{kind}:{source}".encode()).hexdigest()
    anomaly, created = AttendanceAnomaly.objects.get_or_create(
        fingerprint=fingerprint, defaults={"employee": employee, "date": day, "kind": kind, "evidence": evidence})
    return anomaly, created


def reviewable(user):
    queryset = scope_queryset(AttendanceAnomaly.objects.select_related("employee"), user, "employee__organization")
    if user.has_perm(roles.REVIEW_CORRECTIONS):
        return queryset.exclude(employee__user=user)
    profile = getattr(user, "employee_profile", None)
    return (queryset.filter(employee__manager=profile).exclude(employee__user=user)
            if profile and profile.is_active else queryset.none())


@transaction.atomic
def decide(anomaly, *, status, note, by):
    if status not in ("RESOLVED", "DISMISSED") or not note.strip():
        raise ValidationError("Choose a decision and explain the review.")
    if not reviewable(by).filter(pk=anomaly.pk).exists():
        raise PermissionDenied
    anomaly = AttendanceAnomaly.objects.select_for_update().get(pk=anomaly.pk)
    if anomaly.status != "OPEN":
        raise ValidationError("This exception has already been reviewed.")
    anomaly.status = status
    anomaly.save(update_fields=["status"])
    AnomalyDecision.objects.create(anomaly=anomaly, status=status, note=note.strip()[:255], actor=by)
    return anomaly


def scan(start, end, *, user=None, late_threshold=3, max_shift_hours=16):
    if end < start or (end - start).days > 61:
        raise ValidationError("Choose an ordered range of at most 62 days.")
    if user is not None and not user.has_perm(roles.VIEW_ALL_ATTENDANCE):
        raise PermissionDenied
    people = Employee.objects.all()
    if user is not None:
        people = scope_queryset(people, user)
    queryset = Attendance.objects.filter(emp__in=people, date__range=(start, end)).select_related(
        "emp__work_schedule", "emp__department__work_schedule", "emp__work_location")
    resolver, late, count = ScheduleResolver(), defaultdict(list), 0
    from django.utils import timezone
    scan_started = timezone.now()
    employees = {}
    for item in queryset.iterator(chunk_size=500):
        employee = item.emp
        employees[employee.pk] = employee
        if item.check_in and not item.check_out and item.date < timeutil.today_for(employee):
            count += record(employee, item.date, "MISSING_CHECKOUT", {"attendance_id": item.pk})[1]
        if item.hours is not None and item.hours > max_shift_hours:
            count += record(employee, item.date, "LONG_SHIFT", {"attendance_id": item.pk, "hours": item.hours})[1]
        result = evaluate(item, resolver.for_employee(employee))
        if result and result.late_minutes:
            late[employee.pk].append(item.date)
    for employee_id, dates in late.items():
        if len(dates) >= late_threshold:
            count += record(employees[employee_id], max(dates), "REPEATED_LATE",
                            {"count": len(dates), "from": min(dates).isoformat(), "to": max(dates).isoformat()})[1]
    audits = AttendanceAudit.objects.filter(emp__in=people, date__range=(start, end),
        action__in=["MANUAL_CREATE", "MANUAL_EDIT", "MANUAL_DELETE"]).annotate(reason_length=Length("reason")).filter(reason_length__lt=10).select_related("emp")
    for audit in audits.iterator(chunk_size=500):
        count += record(audit.emp, audit.date, "BRIEF_EXPLANATION",
                        {"audit_id": audit.pk, "reason": audit.reason}, source=f"audit:{audit.pk}")[1]
    requests = AttendanceCorrectionRequest.objects.filter(emp__in=people, date__range=(start, end)).annotate(
        reason_length=Length("reason")).filter(reason_length__lt=10).select_related("emp")
    for request in requests.iterator(chunk_size=500):
        count += record(request.emp, request.date, "BRIEF_EXPLANATION",
                        {"correction_request_id": request.pk, "reason": request.reason}, source=f"request:{request.pk}")[1]
    from django.db.models import Count
    from apps.organizations.models import Organization
    from .notifications import notify_attendance_reviewers
    summaries = AttendanceAnomaly.objects.filter(detected_at__gte=scan_started).values(
        "employee__organization_id").annotate(total=Count("pk"))
    for summary in summaries:
        organization = Organization.objects.get(pk=summary["employee__organization_id"])
        notify_attendance_reviewers(organization, count=summary["total"], day=end)
    return count
