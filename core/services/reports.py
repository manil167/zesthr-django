import csv
from datetime import date

from django.db.models import Count, Q

from apps.organizations.tenancy import scope_queryset
from ..models import Attendance, Employee


def attendance_report(
    start_date,
    end_date,
    employee=None,
    department=None,
    user=None,
):
    qs = Attendance.objects.filter(
        date__range=(start_date, end_date)
    ).select_related(
        "emp",
        "emp__department",
    )

    if user is not None:
        qs = scope_queryset(qs, user, "emp__organization")
    if employee:
        qs = qs.filter(
            emp=employee
        )

    if department:
        qs = qs.filter(
            emp__department=department
        )

    return qs.order_by(
        "-date",
        "emp__name",
    )


def employee_summary(
    start_date,
    end_date,
):
    return (
        Employee.objects
        .filter(is_active=True)
        .annotate(
            present=Count(
                "attendance",
                filter=Q(
                    attendance__date__range=(
                        start_date,
                        end_date,
                    ),
                    attendance__status="P",
                ),
            ),
            absent=Count(
                "attendance",
                filter=Q(
                    attendance__date__range=(
                        start_date,
                        end_date,
                    ),
                    attendance__status="A",
                ),
            ),
            half_day=Count(
                "attendance",
                filter=Q(
                    attendance__date__range=(
                        start_date,
                        end_date,
                    ),
                    attendance__status="H",
                ),
            ),
            leave=Count(
                "attendance",
                filter=Q(
                    attendance__date__range=(
                        start_date,
                        end_date,
                    ),
                    attendance__status="L",
                ),
            ),
        )
        .select_related(
            "department",
            "position",
        )
        .order_by("name")
    )


def export_csv(rows):
    response_rows = []

    for row in rows:
        response_rows.append(
            [
                row.date,
                row.emp.employee_number,
                row.emp.name,
                row.emp.department.name
                if row.emp.department
                else "",
                row.get_status_display(),
                row.check_in or "",
                row.check_out or "",
            ]
        )

    return response_rows