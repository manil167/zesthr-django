"""Read models for organization-scoped screens.

Department remains in `core.models` during the incremental migration. This
module is its new owning query boundary; callers outside the legacy facade
should import selectors from here rather than query the model directly.
"""
from django.db.models import Count, Q

from core.models import Department
from .tenancy import scope_queryset


def department_attendance_summary(day, *, user=None):
    """Return active departments annotated with their attendance for `day`."""
    return (
        (scope_queryset(Department.objects.all(), user) if user is not None else Department.objects.all())
        .filter(is_active=True)
        .annotate(
            employee_count=Count(
                "employees",
                filter=Q(employees__is_active=True),
            ),
            present_count=Count(
                "employees__attendance",
                filter=Q(
                    employees__attendance__date=day,
                    employees__attendance__status="P",
                ),
            ),
            absent_count=Count(
                "employees__attendance",
                filter=Q(
                    employees__attendance__date=day,
                    employees__attendance__status="A",
                ),
            ),
        )
        .order_by("name")
    )
