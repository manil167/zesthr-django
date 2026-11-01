from core.permissions import get_employee_profile
from apps.organizations.tenancy import scope_queryset
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ..models import (
    Attendance,
    Employee,
    LeaveApplication,
    Salary,
)

from .permissions import (
    CanViewAttendance,
    CanViewEmployees,
    CanViewPayroll,
)

from .serializers import (
    AttendanceSerializer,
    EmployeeSerializer,
    LeaveApplicationSerializer,
    SalarySerializer,
)


class TenantReadOnlyViewSet(viewsets.ReadOnlyModelViewSet):
    def filter_queryset(self, queryset):
        field = "organization" if queryset.model is Employee else "emp__organization"
        return super().filter_queryset(scope_queryset(queryset, self.request.user, field))


class EmployeeViewSet(TenantReadOnlyViewSet):
    serializer_class = EmployeeSerializer
    permission_classes = [
        IsAuthenticated,
        CanViewEmployees,
    ]

    def get_queryset(self):
        return (
            Employee.objects
            .filter(is_active=True)
            .select_related(
                "department",
                "position",
            )
            .order_by("name")
        )


class AttendanceViewSet(TenantReadOnlyViewSet):
    serializer_class = AttendanceSerializer
    permission_classes = [
        IsAuthenticated,
        CanViewAttendance,
    ]

    def get_queryset(self):
        return (
            Attendance.objects
            .select_related("emp")
            .order_by("-date")
        )


class LeaveApplicationViewSet(
    TenantReadOnlyViewSet
):
    serializer_class = LeaveApplicationSerializer
    permission_classes = [
        IsAuthenticated,
    ]

    def get_queryset(self):
        employee = get_employee_profile(self.request.user)

        if self.request.user.has_perm(
            "core.view_all_leaves"
        ):
            return LeaveApplication.objects.select_related(
                "emp"
            )

        if not employee:
            return LeaveApplication.objects.none()

        return LeaveApplication.objects.filter(
            emp=employee
        ).select_related("emp")


class SalaryViewSet(TenantReadOnlyViewSet):
    serializer_class = SalarySerializer
    permission_classes = [
        IsAuthenticated,
    ]

    def get_queryset(self):
        employee = get_employee_profile(self.request.user)

        if self.request.user.has_perm(
            "core.view_all_payslips"
        ):
            return Salary.objects.select_related(
                "emp"
            )

        if not employee:
            return Salary.objects.none()

        return Salary.objects.filter(
            emp=employee
        ).select_related("emp")