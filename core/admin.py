from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from apps.organizations.tenancy import scope_queryset
from apps.organizations.admin_base import TenantAdmin

from . import roles
from .services import accounts
from .models import (AttendanceAudit, SecurityEvent, Attendance, Department, Employee, EmploymentType, Holiday, JobPosition, Salary, ScheduledEmploymentChange, WorkLocation,
                     WorkSchedule)


@admin.register(Employee)
class EmployeeAdmin(TenantAdmin):
    """The admin site must not be a back door around the role rules."""

    list_display = ("employee_number", "name", "organization", "department", "position", "employment_status")
    list_filter = ("organization", "employment_status", "department")
    search_fields = ("name", "designation", "employee_number")

    # Lifecycle changes go through the app's Deactivate/Reactivate actions, which also
    # revoke or restore the login; the admin must not bypass them.
    LIFECYCLE_FIELDS = ["employment_status", "separation_date", "separation_reason"]

    def get_fields(self, request, obj=None):
        fields = ["user", "employee_number", "name", "designation", "basic_salary", "email", "phone",
                  "joined_on", "preferred_name", "date_of_birth", "organization", "department", "position", "employment_type", "work_location",
                  "work_schedule", "manager", "confirmation_date", *self.LIFECYCLE_FIELDS]
        u = request.user
        if not (u.has_perm(roles.VIEW_EMPLOYEE_PAY) or u.has_perm(roles.CHANGE_EMPLOYEE_PAY)):
            fields.remove("basic_salary")
        if u.has_perm(roles.VIEW_EMPLOYEE_PERSONAL):
            fields += ["address", "emergency_contact_name", "emergency_contact_phone"]
        return fields

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.LIFECYCLE_FIELDS)
        u = request.user
        # Visible-but-locked for users who may see pay but not change it. (Users who
        # may not even see it never get the field: see get_fields.)
        if u.has_perm(roles.VIEW_EMPLOYEE_PAY) and not u.has_perm(roles.CHANGE_EMPLOYEE_PAY):
            readonly.append("basic_salary")
        if not request.user.has_perm(roles.LINK_EMPLOYEE_ACCOUNT):
            readonly.append("user")
        return readonly

    def has_add_permission(self, request):
        # A new employee needs a salary, so it also needs pay-edit rights.
        return (super().has_add_permission(request)
                and request.user.has_perm(roles.CHANGE_EMPLOYEE_PAY))


for model in (Department, JobPosition, EmploymentType, WorkLocation, WorkSchedule):
    admin.site.register(model, TenantAdmin)


@admin.register(Holiday)
class HolidayAdmin(TenantAdmin):
    list_display = ("name", "date", "holiday_type")
    list_filter = ("holiday_type",)
    search_fields = ("name", "description")
    date_hierarchy = "date"


@admin.register(Attendance)
class AttendanceAdmin(TenantAdmin):
    """Read-only here: every change must go through the app, which demands a reason and keeps history."""

    list_display = ("emp", "date", "status", "check_in", "check_out")
    list_filter = ("status",)
    date_hierarchy = "date"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AttendanceAudit)
class AttendanceAuditAdmin(TenantAdmin):
    list_display = ("created_at", "emp", "date", "action", "reason", "changed_by")
    list_filter = ("action",)
    search_fields = ("emp__name", "reason")
    date_hierarchy = "created_at"

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(roles.VIEW_ATTENDANCE_AUDIT)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Salary)
class SalaryAdmin(TenantAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False



@admin.register(ScheduledEmploymentChange)
class ScheduledEmploymentChangeAdmin(admin.ModelAdmin):
    list_display = ("employee", "effective_date", "designation", "department", "manager", "applied_at")
    list_filter = ("effective_date",)
    search_fields = ("employee__name", "reason", "designation")
    date_hierarchy = "effective_date"
    readonly_fields = ("applied_at",)

    def get_queryset(self, request):
        return scope_queryset(super().get_queryset(request), request.user, "employee__organization")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in ("employee", "manager"):
            kwargs["queryset"] = scope_queryset(Employee.objects.all(), request.user)
        elif db_field.name == "department":
            kwargs["queryset"] = scope_queryset(Department.objects.all(), request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and not (obj and obj.applied_at)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and not (obj and obj.applied_at)


admin.site.unregister(User)


@admin.register(User)
class HROSUserAdmin(UserAdmin):
    """Users created here are 'provisioned': they must choose their own password at first sign-in."""

    actions = ["require_password_change", "deactivate_accounts", "activate_accounts"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        # Account operators cannot reset or impersonate privileged users.
        from apps.organizations.tenancy import organization_ids_for
        return queryset.filter(organization_memberships__organization_id__in=organization_ids_for(request.user),
                               organization_memberships__is_active=True,
                               is_superuser=False, is_staff=False, user_permissions__isnull=True).exclude(
            groups__permissions__isnull=False).distinct()

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            fields += ["is_superuser", "is_staff", "groups", "user_permissions"]
        return fields

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate accounts to preserve attributable history


    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            from django.core.exceptions import PermissionDenied
            if obj.is_superuser or obj.is_staff:
                raise PermissionDenied("Only platform administrators may grant privileged access.")
            if change and not self.get_queryset(request).filter(pk=obj.pk).exists():
                raise PermissionDenied
        super().save_model(request, obj, form, change)
        if not change:
            accounts.require_password_change(obj, by=request.user)

    @admin.action(description="Require a password change at next sign-in")
    def require_password_change(self, request, queryset):
        for user in queryset:
            accounts.require_password_change(user, by=request.user)
        self.message_user(request, f"{queryset.count()} account(s) must change their password.")

    @admin.action(description="Deactivate selected accounts (revokes access immediately)")
    def deactivate_accounts(self, request, queryset):
        done = 0
        for user in queryset.exclude(pk=request.user.pk):  # never lock yourself out
            accounts.set_account_active(user, False, by=request.user)
            done += 1
        if queryset.filter(pk=request.user.pk).exists():
            self.message_user(request, "You cannot deactivate your own account.", messages.WARNING)
        self.message_user(request, f"{done} account(s) deactivated.")

    @admin.action(description="Activate selected accounts")
    def activate_accounts(self, request, queryset):
        for user in queryset:
            accounts.set_account_active(user, True, by=request.user)
        self.message_user(request, f"{queryset.count()} account(s) activated.")


@admin.register(SecurityEvent)
class SecurityEventAdmin(TenantAdmin):
    """Read-only audit trail: nobody can add, edit or delete entries from here."""

    list_display = ("created_at", "event_type", "username", "ip_address", "detail")
    list_filter = ("event_type",)
    search_fields = ("username", "ip_address")
    date_hierarchy = "created_at"

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(roles.VIEW_SECURITY_LOG)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import LeaveDecision


@admin.register(LeaveDecision)
class LeaveDecisionAdmin(TenantAdmin):
    list_display = ("leave", "status", "decided_by", "created_at")

    def get_queryset(self, request):
        return scope_queryset(LeaveDecision.objects.select_related("leave__emp", "decided_by"),
                              request.user, "leave__emp__organization")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(roles.APPROVE_LEAVE)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import LeaveEntitlement, LeaveBalanceAdjustment


@admin.register(LeaveEntitlement)
class LeaveEntitlementAdmin(TenantAdmin):
    list_display = ("employee", "leave_type", "year", "annual_days", "monthly_accrual")
    list_filter = ("year", "leave_type")


@admin.register(LeaveBalanceAdjustment)
class LeaveBalanceAdjustmentAdmin(TenantAdmin):
    list_display = ("entitlement", "days", "reason", "changed_by", "created_at")
    readonly_fields = ("changed_by", "created_at")

    def save_model(self, request, obj, form, change):
        obj.changed_by = request.user
        obj.save()

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import PayrollComponent, EmployeePayrollComponent, PayrollAdjustment, PayrollRun, PayrollEvent


@admin.register(PayrollComponent, EmployeePayrollComponent, PayrollAdjustment)
class PayrollConfigurationAdmin(TenantAdmin):
    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(roles.VIEW_EMPLOYEE_PAY)

    def has_add_permission(self, request):
        return request.user.has_perm(roles.CHANGE_EMPLOYEE_PAY)

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm(roles.CHANGE_EMPLOYEE_PAY)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if isinstance(obj, PayrollAdjustment) and not change:
            obj.created_by = request.user
        obj.save()

    def get_readonly_fields(self, request, obj=None):
        return ("created_by", "created_at") if self.model is PayrollAdjustment else ()


@admin.register(PayrollRun, PayrollEvent)
class PayrollHistoryAdmin(TenantAdmin):
    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(roles.VIEW_PAYROLL_REPORT)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Group permissions are platform configuration, never tenant-controlled.
from django.contrib.auth.models import Group
from django.contrib.auth.admin import GroupAdmin
admin.site.unregister(Group)


@admin.register(Group)
class PlatformGroupAdmin(GroupAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
