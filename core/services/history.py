"""Employment history: records what changed on an employee's job record, by whom."""
from ..models import Employee, EmploymentHistory

# field name -> label shown to people. basic_salary is flagged sensitive.
TRACKED = {
    "designation": "Designation", "organization": "Organization", "company": "Company", "branch": "Branch",
    "department": "Department", "team": "Team", "position": "Position", "job_grade": "Job grade",
    "cost_center": "Cost center",
    "employment_type": "Employment type", "work_location": "Work location", "work_schedule": "Work schedule",
    "manager": "Manager", "employment_status": "Status", "basic_salary": "Basic salary",
}
SENSITIVE = {"basic_salary"}


def _text(employee, field):
    value = getattr(employee, field)
    if value is None or value == "":
        return ""
    if field == "employment_status":
        return employee.get_employment_status_display()
    if field == "basic_salary":
        return f"{value:.2f}"
    return str(value)


def snapshot(employee):
    """Readable values of every tracked field, as they are right now."""
    return {field: _text(employee, field) for field in TRACKED}


def record_changes(before, employee, by=None):
    """Write one row per tracked field that differs between `before` (a snapshot) and `employee`."""
    after = snapshot(employee)
    rows = [EmploymentHistory(employee=employee, field=field, old_value=before[field][:255],
                              new_value=after[field][:255], sensitive=field in SENSITIVE, changed_by=by)
            for field in TRACKED if before.get(field) != after[field]]
    EmploymentHistory.objects.bulk_create(rows)
    return len(rows)


def record_created(employee, by=None):
    EmploymentHistory.objects.create(employee=employee, field="created", new_value="Employee record created",
                                     changed_by=by)


def label(field):
    return TRACKED.get(field, "Record created" if field == "created" else field)


def refresh_and_snapshot(pk):
    """Snapshot from the database (before a form mutates the in-memory instance)."""
    return snapshot(Employee.objects.select_related(
        "organization", "company", "branch", "department", "team", "position", "job_grade", "cost_center",
        "employment_type", "work_location", "work_schedule", "manager").get(pk=pk))
