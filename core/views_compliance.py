"""Paginated, tenant-scoped activity reports without exposing salary amounts."""
import csv
from datetime import datetime, time, timedelta
from django import forms
from django.http import HttpResponse
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone
from apps.organizations.tenancy import scope_queryset
from . import roles
from .models import AttendanceAudit, LeaveDecision, PayrollEvent, DocumentEvent, AnomalyDecision
from .permissions import permission_required
from .services.csv_export import safe_cell

SOURCES = [("attendance", "Attendance changes"), ("leave", "Leave decisions"),
           ("payroll", "Payroll lifecycle"), ("documents", "Document activity"),
           ("exceptions", "Attendance exception reviews")]


class AuditFilter(forms.Form):
    source = forms.ChoiceField(choices=SOURCES)
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        if start and end and (end < start or (end - start).days > 365):
            raise forms.ValidationError("Choose an ordered range of at most 366 days.")
        return data


def _records(source, user):
    definitions = {
        "attendance": (AttendanceAudit.objects.select_related("emp", "changed_by"), "emp__organization", "changed_by"),
        "leave": (LeaveDecision.objects.select_related("leave__emp", "decided_by"), "leave__emp__organization", "decided_by"),
        "payroll": (PayrollEvent.objects.select_related("run__organization", "actor"), "run__organization", "actor"),
        "documents": (DocumentEvent.objects.select_related("document__employee", "actor"), "document__employee__organization", "actor"),
        "exceptions": (AnomalyDecision.objects.select_related("anomaly__employee", "actor"), "anomaly__employee__organization", "actor"),
    }
    queryset, path, actor = definitions[source]
    return scope_queryset(queryset, user, path), actor


def _row(item, source, actor, user):
    action = getattr(item, "action", getattr(item, "status", ""))
    if source == "attendance":
        subject, reason = f"Attendance {item.record_id} / employee {item.emp.employee_number}", item.reason
    elif source == "leave":
        subject = f"Leave {item.leave_id} / employee {item.leave.emp.employee_number}"
        reason = item.note if user.has_perm(roles.VIEW_ALL_LEAVES) else "Decision recorded"
    elif source == "payroll":
        subject = f"Payroll {item.run.month}/{item.run.year} / {item.run.organization}"
        reason = item.reason if user.has_perm(roles.VIEW_PAYROLL_REPORT) else "Lifecycle event recorded"
    elif source == "documents":
        subject = f"Document {item.document_id} / employee {item.document.employee.employee_number}"
        reason = item.reason if user.has_perm(roles.VIEW_EMPLOYEE_PERSONAL) else "Activity recorded"
    else:
        subject, reason = f"Exception {item.anomaly_id}", item.note
    person = getattr(item, actor)
    return [item.created_at.isoformat(), person.username if person else "System or historical account",
            action, subject, reason]


@permission_required(roles.VIEW_ATTENDANCE_AUDIT)
def report(request):
    today = timezone.localdate()
    values = {"source": "attendance", "start": today - timedelta(days=30), "end": today}
    values.update(request.GET.dict())
    form = AuditFilter(values)
    page, rows, filter_query = None, [], ""
    if form.is_valid():
        source, start, end = (form.cleaned_data[key] for key in ("source", "start", "end"))
        from urllib.parse import urlencode
        filter_query = urlencode({"source": source, "start": start.isoformat(), "end": end.isoformat()})
        queryset, actor = _records(source, request.user)
        start_at = timezone.make_aware(datetime.combine(start, time.min))
        end_at = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
        queryset = queryset.filter(created_at__gte=start_at, created_at__lt=end_at).order_by("-created_at", "-pk")
        if request.GET.get("export") == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="audit-{source}.csv"'
            response["Cache-Control"] = "private, no-store"
            writer = csv.writer(response)
            writer.writerow(["Timestamp", "Actor", "Action", "Record", "Explanation"])
            for item in queryset.iterator(chunk_size=500):
                writer.writerow([safe_cell(cell) for cell in _row(item, source, actor, request.user)])
            return response
        page = Paginator(queryset, 50).get_page(request.GET.get("page"))
        rows = [_row(item, source, actor, request.user) for item in page]
    return render(request, "compliance_report.html", {"form": form, "rows": rows, "page": page, "filter_query": filter_query})
