from rest_framework import serializers

from ..models import (
    Attendance,
    Employee,
    LeaveApplication,
    Salary,
)


class EmployeeSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(
        source="department.name",
        read_only=True,
    )

    position_name = serializers.CharField(
        source="position.title",
        read_only=True,
    )

    class Meta:
        model = Employee

        fields = [
            "id",
            "employee_number",
            "name",
            "designation",
            "email",
            "phone",
            "department_name",
            "position_name",
            "employment_status",
            "joined_on",
        ]

        read_only_fields = [
            "id",
            "employee_number",
            "employment_status",
        ]


class AttendanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="emp.name",
        read_only=True,
    )

    class Meta:
        model = Attendance

        fields = [
            "id",
            "emp",
            "employee_name",
            "date",
            "status",
            "check_in",
            "check_out",
            "ends_next_day",
        ]

        read_only_fields = [
            "id",
            "employee_name",
        ]


class LeaveApplicationSerializer(
    serializers.ModelSerializer
):
    employee_name = serializers.CharField(
        source="emp.name",
        read_only=True,
    )

    class Meta:
        model = LeaveApplication

        fields = [
            "id",
            "emp",
            "employee_name",
            "leave_type",
            "start_date",
            "end_date",
            "reason",
            "status",
            "admin_note",
            "applied_on",
        ]

        read_only_fields = [
            "id",
            "employee_name",
            "status",
            "admin_note",
            "applied_on",
        ]


class SalarySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="emp.name",
        read_only=True,
    )

    class Meta:
        model = Salary

        fields = [
            "id",
            "emp",
            "employee_name",
            "month",
            "year",
            "absent_days",
            "per_day_pay",
            "gross",
            "deduction",
            "overtime_minutes",
            "overtime_pay",
            "net",
        ]

        read_only_fields = fields