from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from apps.organizations.tenancy import scope_queryset
from .models import Employee
from .permissions import employee_access, require_employee_profile
from .services.leave_balances import balance


@login_required
def balances(request, pk=None):
    employee = (get_object_or_404(scope_queryset(Employee.objects.all(), request.user), pk=pk)
                if pk is not None else require_employee_profile(request.user, active=False))
    if not employee_access(request.user, employee).employment:
        raise PermissionDenied
    year = timezone.localdate().year
    rows = [balance(item) for item in employee.leave_entitlements.filter(year=year)]
    return render(request, "leave_balances.html", {"employee": employee, "balances": rows, "year": year})


@login_required
def cancel_pending(request, pk):
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.shortcuts import redirect
    from django.views.decorators.http import require_POST
    from .models import LeaveApplication
    from .services.leave import cancel_leave
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(LeaveApplication, pk=pk, emp__user=request.user)
    try:
        cancel_leave(item, by=request.user, note=request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Pending leave cancelled.")
    return redirect("leaves_list")
