from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from apps.organizations.tenancy import scope_queryset
from . import roles
from .models import AttendanceAnomaly
from .services import anomalies


@login_required
def queue(request):
    can_scan = request.user.has_perm(roles.VIEW_ALL_ATTENDANCE)
    if can_scan:
        queryset = scope_queryset(AttendanceAnomaly.objects.select_related("employee"),
                                  request.user, "employee__organization")
    else:
        queryset = anomalies.reviewable(request.user)
        if not queryset.exists():
            raise PermissionDenied
    if request.method == "POST":
        if not can_scan:
            raise PermissionDenied
        today = timezone.localdate()
        count = anomalies.scan(today - timedelta(days=30), today, user=request.user)
        messages.success(request, f"{count} new reviewable exception(s) identified.")
        return redirect("anomaly_queue")
    status = request.GET.get("status", "OPEN")
    if status in dict(AttendanceAnomaly.STATUSES):
        queryset = queryset.filter(status=status)
    return render(request, "anomaly_queue.html", {
        "items": Paginator(queryset, 50).get_page(request.GET.get("page")),
        "status": status, "statuses": AttendanceAnomaly.STATUSES, "can_scan": can_scan})


@login_required
def review(request, pk):
    item = get_object_or_404(anomalies.reviewable(request.user), pk=pk)
    if request.method == "POST":
        try:
            anomalies.decide(item, status=request.POST.get("status", ""), note=request.POST.get("note", ""), by=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Attendance exception review recorded.")
        return redirect("anomaly_queue")
    return render(request, "anomaly_review.html", {"item": item})
