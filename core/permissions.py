"""Reusable authorization helpers.

Authentication (who are you?) and authorization (what may you do?) are kept
separate: views declare the permission they need, and anything else is refused
on the server regardless of what the UI shows.

- Anonymous visitors are sent to the login page.
- Logged-in users who lack the permission get HTTP 403.
"""
from functools import wraps
from types import SimpleNamespace

from django.contrib.auth.mixins import PermissionRequiredMixin  # noqa: F401  (re-exported)
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

__all__ = ["PermissionRequiredMixin", "get_employee_profile", "permission_required",
           "require_employee_profile", "require_perm", "employee_access"]


def permission_required(*perms):
    """Function-view decorator: require ALL given permissions."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not request.user.has_perms(perms):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def get_employee_profile(user):
    """The Employee record linked to this login, or None."""
    if not user.is_authenticated:
        return None
    profile = getattr(user, "employee_profile", None)
    if profile is not None:
        from apps.organizations.tenancy import organization_ids_for
        if profile.organization_id not in organization_ids_for(user):
            return None
    return profile


def employee_access(user, employee):
    """What `user` may see of `employee`'s profile. Four tiers, widest to narrowest:

    basic       name, department, position, manager, work contact: the employee themself,
                their direct manager, and anyone with the view_employee permission
    employment  schedule, dates, history, login account: the employee and view_employee holders
    personal    address, emergency contacts: the employee and view_employee_personal holders
    pay         salary and pay history: the employee and view_employee_pay holders
    """
    from . import roles

    profile = get_employee_profile(user)
    is_self = profile is not None and profile.pk == employee.pk
    is_manager = profile is not None and employee.manager_id == profile.pk  # direct reports only
    from apps.organizations.tenancy import organization_ids_for
    if employee.organization_id not in organization_ids_for(user):
        return SimpleNamespace(can_view=False, is_self=False, is_manager=False,
                               employment=False, personal=False, pay=False)
    sees_hr = user.has_perm(roles.VIEW_EMPLOYEE)
    return SimpleNamespace(
        can_view=is_self or is_manager or sees_hr, is_self=is_self, is_manager=is_manager,
        employment=is_self or sees_hr,
        personal=is_self or user.has_perm(roles.VIEW_EMPLOYEE_PERSONAL),
        pay=is_self or user.has_perm(roles.VIEW_EMPLOYEE_PAY),
    )


def require_employee_profile(user, *, active=True):
    """Self-service actions need a real employee profile linked to the login.

    There is deliberately NO fallback to "some other employee": if the account
    is not linked (or the employee is deactivated, when `active=True`), refuse.
    """
    profile = get_employee_profile(user)
    if profile is None:
        raise PermissionDenied("No employee profile is linked to your account. Please contact HR.")
    if active and not profile.is_active:
        raise PermissionDenied("Your employee record is inactive. Please contact HR.")
    return profile


def require_perm(user, perm):
    """Raise 403 unless the user holds `perm` (for checks inside a view)."""
    if not user.has_perm(perm):
        raise PermissionDenied
