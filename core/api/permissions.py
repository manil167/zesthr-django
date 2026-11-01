from rest_framework.permissions import BasePermission

from .. import roles


class CanViewEmployees(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.has_perm(
                roles.VIEW_EMPLOYEE
            )
        )


class CanViewAttendance(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.has_perm(
                roles.VIEW_ALL_ATTENDANCE
            )
        )


class CanViewPayroll(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.has_perm(
                roles.VIEW_PAYROLL_REPORT
            )
        )