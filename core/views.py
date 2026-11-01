import calendar
from datetime import date, timedelta
import csv

from django.http import HttpResponse

from .services import reports as reports_service
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import ProtectedError, Q, Sum
from django.db import connection
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_safe
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from apps.organizations.tenancy import scope_queryset
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from . import roles, selectors, timeutil
from .forms import (AttendanceForm, BulkAttendanceForm, CorrectionRequestForm, DeactivateEmployeeForm, EmployeeForm,
                    EmployeeSelfForm, LeaveApplicationForm)
from .models import (Attendance, AttendanceAudit, AttendanceCorrectionRequest, Department, Employee, Holiday,
                     LeaveApplication, Salary, ScheduledEmploymentChange)
from .permissions import (PermissionRequiredMixin, employee_access, get_employee_profile, permission_required,
                          require_employee_profile, require_perm)
from .services import attendance as attendance_service
from .services import audit as audit_service
from .services import corrections as corrections_service
from .services import exceptions as exceptions_service
from .services import employees as employee_service
from .services import history as history_service
from .services import leave as leave_service
from .services import payroll as payroll_service
from .services import schedules as schedules_service

def period(request):
    """Month/year filter shared by report, attendance log and payroll."""
    today = timezone.localdate()
    try:
        month, year = int(request.GET.get("month", today.month)), int(request.GET.get("year", today.year))
        if not 1 <= month <= 12 or not 2000 <= year <= 2100:
            raise ValueError
    except ValueError:
        month, year = today.month, today.year
    return {
        "month": month, "year": year,
        "months": [(i, calendar.month_name[i]) for i in range(1, 13)],
        "years": range(today.year - 3, today.year + 2),
    }


def home(request):
    """Public landing page. Live company data is shown only to authorized users;
    everyone else sees the template's sample figures."""
    today = timezone.localdate()
    ctx = {"today": today, "month_name": calendar.month_name[today.month], "year": today.year,
           "total_employees": 5, "present_count": 4, "absent_count": 1, "payroll_total": 245000}
    if request.user.has_perm(roles.VIEW_ALL_ATTENDANCE):
        snap = selectors.company_snapshot(today, recent_limit=5, user=request.user)
        ctx.update(total_employees=snap["total"], present_count=snap["present"],
                   absent_count=snap["absent"], leave_count=snap["leave"],
                   unmarked_count=snap["unmarked"], recent_checkins=snap["recent"])
    if request.user.has_perm(roles.VIEW_PAYROLL_REPORT):
        ctx["payroll_total"] = selectors.net_payroll_total(today.month, today.year, user=request.user)
    return render(request, "home.html", ctx)


@login_required
def dashboard(request):
    today = timezone.localdate()

    employee = get_employee_profile(
        request.user
    )

    if (
        employee
        and not request.user.has_perm(
            roles.VIEW_ALL_ATTENDANCE
        )
    ):
        return _employee_portal(
            request,
            employee,
            today,
        )

    if not request.user.has_perm(
        roles.VIEW_ALL_ATTENDANCE
    ):
        raise PermissionDenied

    snapshot = selectors.dashboard_snapshot(
        today, user=request.user
    )

    departments = selectors.department_attendance_summary(
        today, user=request.user
    )

    context = {
        **selectors.company_snapshot(today, user=request.user),
        "payroll": selectors.net_payroll_total(today.month, today.year, user=request.user) if request.user.has_perm(roles.VIEW_PAYROLL_REPORT) else None,
        "today": today,
        "snapshot": snapshot,
        "departments": departments,
        "pending_leaves": (
            snapshot["pending_leaves"]
            if request.user.has_perm(
                roles.VIEW_ALL_LEAVES
            )
            else None
        ),
    }

    return render(
        request,
        "dashboard.html",
        context,
    )


# ---------- Employees ----------
class Base(LoginRequiredMixin, SuccessMessageMixin):
    template_name = "form.html"
    title = ""

    def get_queryset(self):
        queryset = super().get_queryset()
        field = "organization" if self.model is Employee else "emp__organization"
        return scope_queryset(queryset, self.request.user, field)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if "emp" in form.fields:
            form.fields["emp"].queryset = scope_queryset(Employee.objects.all(), self.request.user)
        return form

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["title"] = self.title
        return ctx


class EmployeeList(PermissionRequiredMixin, ListView):
    permission_required = roles.VIEW_EMPLOYEE
    template_name = "employee_list.html"
    context_object_name = "employees"
    paginate_by = 20

    def filters(self):
        g = self.request.GET
        status = g.get("status", "")
        valid = {"active", "inactive", *(code for code, _ in Employee.STATUS)}
        department = g.get("department", "")
        return {"q": g.get("q", "").strip(), "status": status if status in valid else "",
                "department": department if department.isdigit() else ""}

    def get_queryset(self):
        f = self.filters()
        qs = scope_queryset(
            Employee.objects.select_related("organization", "department", "position"),
            self.request.user,
        )
        if f["q"]:
            qs = qs.filter(Q(name__icontains=f["q"]) | Q(designation__icontains=f["q"])
                           | Q(employee_number__icontains=f["q"]) | Q(email__icontains=f["q"])
                           | Q(department__name__icontains=f["q"]) | Q(position__title__icontains=f["q"]))
        if f["status"] in ("active", "inactive"):
            qs = qs.filter(is_active=f["status"] == "active")
        elif f["status"]:
            qs = qs.filter(employment_status=f["status"])
        if f["department"]:
            qs = qs.filter(department_id=int(f["department"]))
        return qs.order_by("name", "pk")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(self.filters(), departments=scope_queryset(Department.objects.all(), self.request.user), statuses=Employee.STATUS,
                   total_matches=ctx["paginator"].count)
        return ctx


@permission_required(roles.CHANGE_EMPLOYEE)
def scheduled_employment_changes(request):
    status = request.GET.get("status", "pending")
    changes = scope_queryset(ScheduledEmploymentChange.objects.select_related(
        "employee", "department", "manager"), request.user, "employee__organization")
    if status == "applied":
        changes = changes.filter(applied_at__isnull=False)
    elif status == "due":
        changes = changes.filter(applied_at__isnull=True, effective_date__lte=timezone.localdate())
    else:
        status = "pending"
        changes = changes.filter(applied_at__isnull=True)
    return render(request, "scheduled_employment_changes.html", {"changes": Paginator(changes, 50).get_page(request.GET.get("page")), "status": status,
                                                                     "today": timezone.localdate()})


@permission_required(roles.CHANGE_EMPLOYEE)
def apply_scheduled_employment_change(request, pk):
    if request.method != "POST":
        raise Http404
    change = get_object_or_404(scope_queryset(
        ScheduledEmploymentChange.objects.all(), request.user, "employee__organization"),
        pk=pk, applied_at__isnull=True)
    if change.effective_date > timezone.localdate():
        messages.error(request, "This change is not effective yet.")
    else:
        try:
            employee_service.apply_scheduled_change(change, by=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Scheduled employment change applied.")
    return redirect("scheduled_employment_changes")


@login_required
def employee_detail(request, pk):
    emp = get_object_or_404(scope_queryset(Employee.objects.select_related(
        "department", "position", "employment_type", "work_location", "work_schedule", "manager", "user"),
        request.user), pk=pk)
    access = employee_access(request.user, emp)
    if not access.can_view:
        raise Http404  # not "forbidden": do not even confirm that this employee exists
    return render(request, "employee_detail.html", _profile_context(emp, access))


def _profile_context(emp, access):
    ctx = {"emp": emp, "access": access, "history": [], "reports": []}
    if access.employment:
        events = emp.history.select_related("changed_by")
        ctx["history"] = [h for h in events if access.pay or not h.sensitive]
        ctx["reports"] = emp.direct_reports.filter(is_active=True).select_related("position")
    return ctx


@login_required
def my_profile(request):
    emp = require_employee_profile(request.user, active=False)
    return render(request, "employee_detail.html", _profile_context(emp, employee_access(request.user, emp)))


@login_required
def my_profile_edit(request):
    emp = require_employee_profile(request.user)
    form = EmployeeSelfForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your details were updated.")
        return redirect("my_profile")
    return render(request, "form.html", {"form": form, "title": "Edit my details"})


class EmployeeFormMixin:
    """Users without pay-edit permission never see or change the salary field."""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["can_edit_pay"] = self.request.user.has_perm(roles.CHANGE_EMPLOYEE_PAY)
        kwargs["can_link_account"] = self.request.user.has_perm(roles.LINK_EMPLOYEE_ACCOUNT)
        kwargs["can_edit_personal"] = self.request.user.has_perm(roles.VIEW_EMPLOYEE_PERSONAL)
        return kwargs


class EmployeeCreate(PermissionRequiredMixin, EmployeeFormMixin, Base, CreateView):
    # Creating an employee requires a salary, so it also needs pay-edit rights.
    permission_required = (roles.ADD_EMPLOYEE, roles.CHANGE_EMPLOYEE_PAY)

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            history_service.record_created(self.object, self.request.user)
        return response
    model, form_class, title = Employee, EmployeeForm, "Add employee"
    success_url, success_message = reverse_lazy("employee_list"), "Employee added."


class EmployeeUpdate(PermissionRequiredMixin, EmployeeFormMixin, Base, UpdateView):
    permission_required = roles.CHANGE_EMPLOYEE

    def form_valid(self, form):
        before = history_service.refresh_and_snapshot(self.object.pk)  # DB values, before the save
        with transaction.atomic():
            response = super().form_valid(form)
            history_service.record_changes(before, self.object, self.request.user)
        return response
    model, form_class, title = Employee, EmployeeForm, "Edit employee"
    success_url, success_message = reverse_lazy("employee_list"), "Employee updated."


class EmployeeDelete(PermissionRequiredMixin, Base, DeleteView):
    permission_required = roles.DELETE_EMPLOYEE

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, "This employee has attendance, leave or payroll history and cannot "
                                         "be deleted. Deactivate the employee instead.")
            return redirect("employee_list")
    model, title, template_name = Employee, "Delete employee", "confirm_delete.html"
    success_url, success_message = reverse_lazy("employee_list"), "Employee deleted."


# ---------- Attendance ----------
def _employee_portal(request, emp, today):
    ctx = selectors.employee_snapshot(emp, timeutil.today_for(emp))  # the employee's own local day
    # Notifications may link to a named route or to (route, id); resolve here so templates stay simple.
    from django.urls import reverse
    for note in ctx["notifications"]:
        target = note["url"]
        note["href"] = (reverse(target[0], args=[target[1]]) if isinstance(target, tuple)
                        else reverse(target) if target else "")
    return render(request, "employee_dashboard.html", ctx)


@login_required
def my_portal(request):
    """The employee view of the dashboard. HR staff who are also employees reach it from here."""
    emp = require_employee_profile(request.user, active=False)
    return _employee_portal(request, emp, timezone.localdate())


@permission_required(roles.DEACTIVATE_EMPLOYEE)
def employee_deactivate(request, pk):
    emp = get_object_or_404(scope_queryset(Employee.objects.all(), request.user), pk=pk)
    if not emp.is_active:
        messages.info(request, f"{emp.name} is already inactive.")
        return redirect("employee_list")
    form = DeactivateEmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            employee_service.deactivate_employee(emp, by=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"{emp.name} deactivated. Their records are kept and their login is disabled.")
            return redirect("employee_list")
    return render(request, "employee_deactivate.html", {"form": form, "emp": emp})


@permission_required(roles.DEACTIVATE_EMPLOYEE)
def employee_reactivate(request, pk):
    emp = get_object_or_404(scope_queryset(Employee.objects.all(), request.user), pk=pk)
    if request.method == "POST":
        employee_service.reactivate_employee(emp, by=request.user)
        messages.success(request, f"{emp.name} reactivated.")
    return redirect("employee_list")


@login_required
def attendance_today(request):
    today = timezone.localdate()
    emp_profile = get_employee_profile(request.user)
    can_mark_others = request.user.has_perm(roles.MARK_ATTENDANCE)

    if request.method == "POST":
        raw_id = request.POST.get("emp_id")
        try:
            emp_id = int(raw_id) if raw_id else None
        except ValueError:
            raise Http404("Unknown employee.")

        if can_mark_others and emp_id is not None:
            emp = get_object_or_404(scope_queryset(Employee.objects.all(), request.user), pk=emp_id)
        else:
            # Self-service: the employee always comes from the logged-in account.
            emp = require_employee_profile(request.user)
            # A submitted id that points at someone else is tampering, not a typo.
            if emp_id is not None and emp_id != emp.pk:
                raise PermissionDenied

        action = request.POST.get("action")
        # Employees may only punch in/out. Setting a status (absent, half day,
        # leave) changes pay, so it is reserved for attendance managers.
        if action in attendance_service.MARKABLE_STATUSES and not can_mark_others:
            raise PermissionDenied

        reason = request.POST.get("reason", "")
        try:
            if action == "check_in":
                rec = attendance_service.check_in(emp, by=request.user)
                messages.success(request, f"Punched IN at {rec.check_in.strftime('%I:%M %p')}.")
            elif action == "check_out":
                rec = attendance_service.check_out(emp, by=request.user)
                messages.success(request, f"Punched OUT at {rec.check_out.strftime('%I:%M %p')}.")
            elif action in attendance_service.MARKABLE_STATUSES:
                rec = attendance_service.mark_status(emp, action, by=request.user, reason=reason)
                messages.success(request, f"{emp.name}: status marked as {rec.get_status_display()}.")
            else:
                # Unknown action: change nothing (it used to create a "Present" record).
                messages.error(request, "Unknown attendance action.")
        except attendance_service.AttendanceError as exc:
            if exc.code in ("already_checked_in", "already_checked_out"):
                from .services.anomalies import record
                record(emp, timeutil.today_for(emp), "DUPLICATE_PUNCH",
                       {"action": action, "message": "Repeated submission; confirm whether this was a retry."})
            messages.error(request, str(exc))

        if not can_mark_others:
            return redirect("dashboard")
        return redirect("attendance_today")

    if not can_mark_others:
        if emp_profile:
            return redirect("dashboard")
        raise PermissionDenied

    return render(request, "attendance_today.html",
                  {"rows": selectors.attendance_register(today, user=request.user), "today": today})


class AttendanceList(LoginRequiredMixin, ListView):
    """Everyone sees their own log; only users with view_all_attendance see others."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (
            request.user.has_perm(roles.VIEW_ALL_ATTENDANCE) or get_employee_profile(request.user)
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    template_name = "attendance_list.html"
    context_object_name = "records"
    paginate_by = 50
    MAX_RANGE_DAYS = 366

    def date_range(self):
        """(from, to) when the user typed a range, else None. A reversed or oversized range is corrected."""
        d1, d2 = _parse_date(self.request.GET.get("from")), _parse_date(self.request.GET.get("to"))
        if not (d1 or d2):
            return None
        d1, d2 = d1 or d2, d2 or d1
        if d2 < d1:
            d1, d2 = d2, d1
        return d1, min(d2, d1 + timedelta(days=self.MAX_RANGE_DAYS - 1))

    def get_queryset(self):
        p, g = period(self.request), self.request.GET
        qs = scope_queryset(Attendance.objects.select_related("emp", "emp__work_schedule", "emp__department__work_schedule"), self.request.user, "emp__organization")
        rng = self.date_range()
        qs = qs.filter(date__range=rng) if rng else qs.filter(date__month=p["month"], date__year=p["year"])
        if g.get("status") in ("P", "A", "H", "L"):
            qs = qs.filter(status=g["status"])
        if not self.request.user.has_perm(roles.VIEW_ALL_ATTENDANCE):
            # Employees see only their own rows, whatever employee/department they ask for.
            return qs.filter(emp=get_employee_profile(self.request.user))
        if g.get("emp", "").isdigit():
            qs = qs.filter(emp_id=int(g["emp"]))
        if g.get("department", "").isdigit():
            qs = qs.filter(emp__department_id=int(g["department"]))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(period(self.request))
        can_see_all = self.request.user.has_perm(roles.VIEW_ALL_ATTENDANCE)
        g, rng = self.request.GET, self.date_range()
        ctx.update(is_staff=can_see_all, emp=g.get("emp", ""), department=g.get("department", ""),
                   status=g.get("status", ""), date_from=rng[0].isoformat() if rng else "",
                   date_to=rng[1].isoformat() if rng else "", statuses=Attendance.STATUS,
                   departments=scope_queryset(Department.objects.all(), self.request.user) if can_see_all else Department.objects.none(),
                   employees=scope_queryset(Employee.objects.all(), self.request.user) if can_see_all else Employee.objects.none(),
                   total_matches=ctx["paginator"].count)
        resolver = schedules_service.ScheduleResolver()
        for rec in ctx["records"]:  # what each day's punches mean under the employee's schedule
            rec.day = schedules_service.evaluate(rec, resolver.for_employee(rec.emp))
        return ctx


class AttendanceCreate(PermissionRequiredMixin, Base, CreateView):
    permission_required = roles.ADD_ATTENDANCE
    model, form_class, title = Attendance, AttendanceForm, "Add attendance entry"
    success_url, success_message = reverse_lazy("attendance_list"), "Attendance saved."

    def get_initial(self):  # links from the register / exception reports pre-fill the form
        g = self.request.GET
        initial = {}
        if g.get("emp", "").isdigit():
            initial["emp"] = int(g["emp"])
        if _parse_date(g.get("date")):
            initial["date"] = _parse_date(g["date"])
        return initial

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            attendance_service.record_manual_change(AttendanceAudit.MANUAL_CREATE, self.object, None,
                                                    by=self.request.user, reason=form.cleaned_data["reason"])
        return response


class AttendanceUpdate(PermissionRequiredMixin, Base, UpdateView):
    permission_required = roles.CHANGE_ATTENDANCE
    model, form_class, title = Attendance, AttendanceForm, "Edit attendance entry"
    success_url, success_message = reverse_lazy("attendance_list"), "Attendance updated."

    def form_valid(self, form):
        # The in-memory object already carries the form's values; the original is what the database holds.
        before = attendance_service.snapshot(Attendance.objects.get(pk=self.object.pk))
        with transaction.atomic():
            response = super().form_valid(form)
            attendance_service.record_manual_change(AttendanceAudit.MANUAL_EDIT, self.object, before,
                                                    by=self.request.user, reason=form.cleaned_data["reason"])
        return response


class AttendanceDelete(PermissionRequiredMixin, Base, DeleteView):
    permission_required = roles.DELETE_ATTENDANCE
    model, title, template_name = Attendance, "Delete attendance entry", "attendance_confirm_delete.html"
    success_url, success_message = reverse_lazy("attendance_list"), "Entry deleted."

    def form_valid(self, form):
        reason = self.request.POST.get("reason", "").strip()
        if not reason:
            return self.render_to_response(self.get_context_data(form=form, reason_error=True))
        rec = self.object
        with transaction.atomic():
            attendance_service.record_manual_change(
                AttendanceAudit.MANUAL_DELETE, rec, attendance_service.snapshot(rec), by=self.request.user,
                reason=reason, deleted=True, day=rec.date, emp=rec.emp)
            return super().form_valid(form)


@require_safe
def healthz(request):
    """For uptime monitors and deploy checks: is the app up, and can it reach its database?
    Public on purpose and reveals nothing beyond ok / unavailable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            return JsonResponse({"status": "unavailable"}, status=503, headers={"Cache-Control": "no-store"})
        response = JsonResponse({"status": "ok"})
    except Exception:
        response = JsonResponse({"status": "unavailable"}, status=503)
    response["Cache-Control"] = "no-store"
    return response


# ---------- Register, bulk entry, corrections, exceptions ----------
def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@permission_required(roles.VIEW_ALL_ATTENDANCE)
def attendance_register(request):
    """Everyone's attendance for one day, with the people who still have no record."""
    g = request.GET
    day = _parse_date(g.get("date")) or timezone.localdate()
    people = (Employee.objects.filter(joined_on__lte=day)
              .filter(Q(is_active=True) | Q(separation_date__gte=day) | Q(attendance__date=day)).distinct()
              .select_related("department", "position", "work_schedule", "department__work_schedule"))
    if g.get("department", "").isdigit():
        people = people.filter(department_id=int(g["department"]))
    if g.get("q", "").strip():
        q = g["q"].strip()
        people = people.filter(Q(name__icontains=q) | Q(employee_number__icontains=q))
    people = scope_queryset(people, request.user)
    records = {r.emp_id: r for r in Attendance.objects.filter(date=day, emp__in=people)}
    holiday = scope_queryset(Holiday.objects.filter(date=day), request.user).first()
    resolver = schedules_service.ScheduleResolver()
    rows, counts = [], {"P": 0, "H": 0, "A": 0, "L": 0, "unmarked": 0, "off": 0}
    for emp in people:
        rec = records.get(emp.pk)
        schedule = resolver.for_employee(emp)
        working = day.weekday() in (schedule.working_days if schedule else [0, 1, 2, 3, 4]) and not holiday
        state = rec.status if rec else ("unmarked" if working else "off")
        counts[state] += 1
        rows.append({"emp": emp, "rec": rec, "state": state, "day": schedules_service.evaluate(rec, schedule) if rec else None})
    wanted = g.get("status", "")
    if wanted:
        rows = [r for r in rows if r["state"] == wanted]
    return render(request, "attendance_register.html", {
        "day": day, "rows": rows, "counts": counts, "holiday": holiday, "q": g.get("q", ""),
        "department": g.get("department", ""), "status": wanted, "departments": scope_queryset(Department.objects.all(), request.user),
        "prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1),
        "can_bulk": request.user.has_perms([roles.ADD_ATTENDANCE, roles.MARK_ATTENDANCE]),
        "can_overwrite": request.user.has_perm(roles.CHANGE_ATTENDANCE),
        "today": timezone.localdate()})


@permission_required(roles.ADD_ATTENDANCE, roles.MARK_ATTENDANCE)
def attendance_bulk(request):
    if request.method != "POST":
        return redirect("attendance_register")
    form = BulkAttendanceForm(request.POST)
    back = reverse("attendance_register")
    if not form.is_valid():
        messages.error(request, "Bulk entry not saved: " + "; ".join(
            f"{name}: {' '.join(errs)}" for name, errs in form.errors.items()))
        return redirect(back)
    data = form.cleaned_data
    back = f"{back}?date={data['date']:%Y-%m-%d}"
    ids = [int(i) for i in request.POST.getlist("emp") if i.isdigit()]
    if not ids:
        messages.error(request, "Tick at least one employee first.")
        return redirect(back)
    if data["overwrite"] and not request.user.has_perm(roles.CHANGE_ATTENDANCE):
        raise PermissionDenied
    try:
        results = attendance_service.bulk_mark(
            scope_queryset(Employee.objects.filter(pk__in=ids).select_related("work_location"), request.user), data["date"], status=data["status"],
            check_in=data["check_in"], check_out=data["check_out"], ends_next_day=data["ends_next_day"],
            by=request.user, reason=data["reason"], overwrite=data["overwrite"])
    except attendance_service.AttendanceError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    done = [r for r in results if r.outcome in ("created", "updated")]
    skipped = [r for r in results if r.outcome == "skipped"]
    unchanged = [r for r in results if r.outcome == "unchanged"]
    messages.success(request, f"{len(done)} employee(s) updated" + (f", {len(unchanged)} already correct" if unchanged else "")
                     + (f", {len(skipped)} skipped." if skipped else "."))
    for r in skipped[:8]:
        messages.warning(request, f"{r.emp.name}: {r.message}")
    return redirect(back)


@login_required
def correction_new(request):
    emp = require_employee_profile(request.user)
    initial = {"date": _parse_date(request.GET.get("date")) or timeutil.today_for(emp), "status": "P"}
    current = Attendance.objects.filter(emp=emp, date=initial["date"]).first()
    if current:
        initial.update(status=current.status, check_in=current.check_in, check_out=current.check_out,
                       ends_next_day=current.ends_next_day)
    form = CorrectionRequestForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            req = corrections_service.submit(emp, by=request.user, **form.cleaned_data)
        except attendance_service.AttendanceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Request sent for {req.date:%d %b %Y}. You will be notified of the decision.")
            return redirect("my_corrections")
    return render(request, "correction_form.html", {"form": form, "window": settings.CORRECTION_WINDOW_DAYS})


@login_required
def my_corrections(request):
    emp = require_employee_profile(request.user, active=False)
    return render(request, "correction_list.html", {
        "mode": "mine", "items": AttendanceCorrectionRequest.objects.filter(emp=emp).select_related("decided_by")[:100]})


@login_required
def correction_cancel(request, pk):
    req = get_object_or_404(AttendanceCorrectionRequest, pk=pk, emp__user=request.user)
    if request.method == "POST":
        try:
            corrections_service.cancel(req, by=request.user)
            messages.info(request, "Request cancelled.")
        except attendance_service.AttendanceError as exc:
            messages.error(request, str(exc))
    return redirect("my_corrections")


@login_required
def correction_list(request):
    """The queue of requests this user may decide: HR sees all, a manager sees their team's."""
    qs = corrections_service.reviewable_requests(request.user)
    if not (request.user.has_perm(roles.REVIEW_CORRECTIONS) or qs.exists()):
        raise PermissionDenied
    status = request.GET.get("status", "PENDING")
    if status in dict(AttendanceCorrectionRequest.STATUSES):
        qs = qs.filter(status=status)
    else:
        status = ""
    return render(request, "correction_list.html", {
        "mode": "review", "items": qs.order_by("created_at" if status == "PENDING" else "-created_at")[:200],
        "status": status, "statuses": AttendanceCorrectionRequest.STATUSES})


@login_required
def correction_decide(request, pk):
    req = get_object_or_404(AttendanceCorrectionRequest.objects.select_related("emp"), pk=pk)
    if not corrections_service.can_review(request.user, req):
        raise Http404  # not your request to see
    if request.method == "POST":
        approve = request.POST.get("action") == "approve"
        try:
            corrections_service.decide(req, approve=approve, note=request.POST.get("note", ""), by=request.user)
            messages.success(request, f"Request {'approved: attendance updated' if approve else 'rejected'}.")
        except attendance_service.AttendanceError as exc:
            messages.error(request, str(exc))
    return redirect(request.POST.get("next") if request.POST.get("next", "").startswith("/") else "correction_list")


@permission_required(roles.VIEW_ATTENDANCE_AUDIT)
def attendance_history(request):
    """Who changed which attendance record, when, from what to what, and why. Read-only."""
    g = request.GET
    qs = scope_queryset(AttendanceAudit.objects.select_related("emp", "emp__user", "changed_by", "correction_request"), request.user, "emp__organization")
    if g.get("emp", "").isdigit():
        qs = qs.filter(emp_id=int(g["emp"]))
    d_from, d_to = _parse_date(g.get("from")), _parse_date(g.get("to"))
    if d_from:
        qs = qs.filter(date__gte=d_from)
    if d_to:
        qs = qs.filter(date__lte=d_to)
    c_from, c_to = _parse_date(g.get("changed_from")), _parse_date(g.get("changed_to"))
    if c_from:
        qs = qs.filter(created_at__date__gte=c_from)
    if c_to:
        qs = qs.filter(created_at__date__lte=c_to)
    action = g.get("action", "")
    if action in dict(AttendanceAudit.ACTIONS):
        qs = qs.filter(action=action)
    else:
        action = ""
    by = g.get("by", "").strip()
    if by:
        qs = qs.filter(changed_by__username__icontains=by)
    q = g.get("q", "").strip()
    if q:
        qs = qs.filter(reason__icontains=q)
    page = Paginator(qs, 50).get_page(g.get("page"))
    for entry in page:
        entry.changes = audit_service.describe(entry)
        entry.by_self = entry.changed_by_id is not None and entry.changed_by_id == entry.emp.user_id
    return render(request, "attendance_history.html", {
        "page": page, "total": page.paginator.count, "employees": scope_queryset(Employee.objects.all(), request.user),
        "actions": AttendanceAudit.ACTIONS, "emp": g.get("emp", ""), "d_from": g.get("from", "") if d_from else "",
        "d_to": g.get("to", "") if d_to else "", "c_from": g.get("changed_from", "") if c_from else "",
        "c_to": g.get("changed_to", "") if c_to else "", "action": action, "by": by, "q": q})


def _exceptions(request, preset):
    g = request.GET
    today = timezone.localdate()
    d_to = _parse_date(g.get("to")) or today - timedelta(days=1)
    d_from = _parse_date(g.get("from")) or d_to - timedelta(days=13)
    default = exceptions_service.MISSING_PUNCH_KINDS if preset == "missing" else exceptions_service.DEFAULT_KINDS
    valid = dict(exceptions_service.KINDS)
    chosen = [k for k in g.getlist("kind") if k in valid] or list(default)
    error, rows = "", []
    try:
        rows = exceptions_service.find_exceptions(
            d_from, d_to, kinds=chosen, department_id=int(g["department"]) if g.get("department", "").isdigit() else None,
            emp_id=int(g["emp"]) if g.get("emp", "").isdigit() else None, user=request.user)
    except ValueError as exc:
        error = str(exc)
    page = Paginator(rows, 50).get_page(g.get("page"))
    return render(request, "attendance_exceptions.html", {
        "page": page, "total": len(rows), "error": error, "kinds": exceptions_service.KINDS, "chosen": chosen,
        "d_from": min(d_from, d_to), "d_to": max(d_from, d_to), "department": g.get("department", ""),
        "emp": g.get("emp", ""), "departments": scope_queryset(Department.objects.all(), request.user), "employees": scope_queryset(Employee.objects.all(), request.user),
        "preset": preset})


@permission_required(roles.VIEW_ALL_ATTENDANCE)
def attendance_exceptions(request):
    return _exceptions(request, "all")


@permission_required(roles.VIEW_ALL_ATTENDANCE)
def attendance_missing_punches(request):
    return _exceptions(request, "missing")


# ---------- Reports & payroll ----------
@permission_required(roles.VIEW_PAYROLL_REPORT)
def monthly_report(request):
    p = period(request)
    rows = payroll_service.calculate_payroll_rows(p["month"], p["year"], user=request.user)
    p.update(rows=rows, total_net=sum(r["net"] for r in rows), total_ded=sum(r["deduction"] for r in rows),
             month_name=calendar.month_name[p["month"]])
    return render(request, "report.html", p)


@permission_required(roles.VIEW_PAYROLL_REPORT)
def payroll(request):
    p = period(request)
    if request.method == "POST":
        require_perm(request.user, roles.GENERATE_PAYROLL)
        try:
            payroll_service.generate_payroll(p["month"], p["year"], user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Draft payroll generated. Review and finalize it in Payroll runs.")
        return redirect(f"{request.path}?month={p['month']}&year={p['year']}")
    slips = scope_queryset(Salary.objects.select_related("emp"), request.user, "emp__organization").filter(month=p["month"], year=p["year"])
    p.update(slips=slips, total=slips.aggregate(t=Sum("net"))["t"] or 0,
             month_name=calendar.month_name[p["month"]],
             any_overtime=slips.filter(overtime_pay__gt=0).exists())
    return render(request, "payroll.html", p)


@login_required
def slip(request, pk):
    emp_profile = get_employee_profile(request.user)
    slips = scope_queryset(Salary.objects.select_related("emp"), request.user, "emp__organization")
    if not request.user.has_perm(roles.VIEW_ALL_PAYSLIPS):
        if not emp_profile:
            raise PermissionDenied
        slips = slips.filter(emp=emp_profile)  # someone else's slip is a 404, not a hint it exists
    s = get_object_or_404(slips, pk=pk)
    counts = payroll_service.attendance_totals(s)
    return render(request, "slip.html", {"s": s, "c": counts, "month_name": calendar.month_name[s.month],
                                         "basic": s.calculation_snapshot.get("basic", s.gross - s.overtime_pay),
                                         "components": s.calculation_snapshot.get("components", []),
                                         "currency": s.calculation_snapshot.get("currency", s.emp.organization.default_currency),
                                         "overtime_text": schedules_service.minutes_text(s.overtime_minutes)})


# ---------- Holidays & Leaves ----------
@login_required
def holidays_list(request):
    today = timezone.localdate()
    holidays = scope_queryset(Holiday.objects.all(), request.user).order_by("date")
    h_type = request.GET.get("type")
    if h_type in ("gazetted", "restricted"):
        holidays = holidays.filter(holiday_type=h_type)
    return render(request, "holidays.html", {
        "holidays": holidays,
        "today": today,
        "selected_type": h_type or "all",
    })


@login_required
def leaves_list(request):
    today = timezone.localdate()
    emp_profile = get_employee_profile(request.user)
    is_manager = bool(emp_profile and emp_profile.is_active and
                      Employee.objects.filter(manager=emp_profile, is_active=True).exists())
    can_manage = request.user.has_perm(roles.VIEW_ALL_LEAVES) or is_manager
    if request.GET.get("mine") == "1" and emp_profile:
        can_manage = False

    if not can_manage and not emp_profile:
        raise PermissionDenied

    if not can_manage:
        leaves = LeaveApplication.objects.filter(emp=emp_profile).order_by("-applied_on")
        sick_used = emp_profile.sick_leaves_used_this_month(today.month, today.year)
        sick_rem = emp_profile.sick_leaves_remaining(today.month, today.year)
        return render(request, "leaves_list.html", {
            "leaves": Paginator(leaves, 50).get_page(request.GET.get("page")),
            "is_staff": False,
            "sick_used": sick_used,
            "sick_rem": sick_rem,
            "emp": emp_profile,
            "today": today,
        })

    status_filter = request.GET.get("status")
    qs = scope_queryset(LeaveApplication.objects.select_related("emp"), request.user, "emp__organization").order_by("-applied_on")
    if not request.user.has_perm(roles.VIEW_ALL_LEAVES):
        qs = leave_service.reviewable_leaves(request.user)
    pending_count = qs.filter(status="PENDING").count()
    if status_filter in dict(LeaveApplication.STATUS):
        qs = qs.filter(status=status_filter)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    review_ids = set(leave_service.reviewable_leaves(request.user).filter(
        pk__in=[item.pk for item in page]).values_list("pk", flat=True))
    for item in page:
        item.can_review = item.pk in review_ids
    return render(request, "leaves_list.html", {
        "leaves": page,
        "is_staff": True,
        "pending_count": pending_count,
        "selected_status": status_filter or "ALL",
        "today": today,
    })


@login_required
def leave_apply(request):
    today = timezone.localdate()
    # The applicant is always the logged-in user's own (active) employee profile.
    emp_profile = require_employee_profile(request.user)

    sick_used = emp_profile.sick_leaves_used_this_month(today.month, today.year)
    sick_rem = emp_profile.sick_leaves_remaining(today.month, today.year)

    if request.method == "POST":
        form = LeaveApplicationForm(request.POST, employee=emp_profile)
        if form.is_valid():
            try:
                leave = leave_service.submit_leave(emp_profile, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Leave application for {leave.days_count} day(s) submitted. Awaiting HR review.")
                return redirect("leaves_list")
    else:
        form = LeaveApplicationForm(employee=emp_profile, initial={"start_date": today, "end_date": today})

    return render(request, "leave_apply.html", {
        "form": form,
        "emp": emp_profile,
        "sick_used": sick_used,
        "sick_rem": sick_rem,
        "today": today,
    })


@login_required
def leave_action(request, pk):

    profile = get_employee_profile(request.user)
    if not (request.user.has_perm(roles.APPROVE_LEAVE) or
            (profile and profile.is_active and Employee.objects.filter(manager=profile).exists())):
        raise PermissionDenied
    leave = get_object_or_404(leave_service.reviewable_leaves(request.user), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        note = request.POST.get("admin_note", "").strip()
        try:
            if action == "approve":
                leave_service.approve_leave(leave, note, by=request.user)
                messages.success(request, "Leave approved and attendance recorded.")
            elif action == "reject":
                leave_service.reject_leave(leave, note, by=request.user)
                messages.info(request, "Leave rejected.")
            elif action == "cancel":
                leave_service.cancel_leave(leave, by=request.user, note=note)
                messages.info(request, "Leave cancelled and attendance restored.")
            else:
                messages.error(request, "Choose a valid leave action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))

    return redirect("leaves_list")


@login_required
@require_safe
def attendance_report_csv(request):
    if not request.user.has_perm(
        roles.VIEW_ALL_ATTENDANCE
    ):
        raise PermissionDenied

    start = _parse_date(
        request.GET.get("from")
    )

    end = _parse_date(
        request.GET.get("to")
    )

    if not start or not end:
        return HttpResponse("Both from and to dates are required.", status=400)

    if end < start or (end - start).days > 365:
        return HttpResponse("Choose an ordered date range of at most 366 days.", status=400)

    rows = reports_service.attendance_report(
        start,
        end, user=request.user,
    )

    response = HttpResponse(
        content_type="text/csv"
    )

    response["Content-Disposition"] = (
        'attachment; filename="attendance-report.csv"'
    )

    writer = csv.writer(response)

    writer.writerow(
        [
            "Date",
            "Employee ID",
            "Employee",
            "Department",
            "Status",
            "Check In",
            "Check Out",
        ]
    )

    from .services.csv_export import safe_cell
    for row in rows.iterator(chunk_size=500):
        writer.writerow(
            [
                row.date,
                safe_cell(row.emp.employee_number),
                safe_cell(row.emp.name),
                safe_cell(row.emp.department.name if row.emp.department else ""),
                row.get_status_display(),
                row.check_in or "",
                row.check_out or "",
            ]
        )

    return response
