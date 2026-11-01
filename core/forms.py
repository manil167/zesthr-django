from datetime import date

from apps.organizations.tenancy import scope_model_queryset
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import Attendance, Employee, LeaveApplication
from .services.leave import validate_leave_request

D = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
T = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["organization", "user", "employee_number", "name", "preferred_name", "date_of_birth", "designation", "basic_salary", "email", "phone", "joined_on",
                  "company", "branch", "department", "team", "position", "job_grade", "cost_center",
                  "employment_type", "work_location", "work_schedule", "manager",
                  "employment_status", "confirmation_date",
                  "address", "emergency_contact_name", "emergency_contact_phone"]
        widgets = {"joined_on": D, "date_of_birth": D, "confirmation_date": D, "address": forms.Textarea(attrs={"rows": 2})}
        labels = {"user": "Login account", "employment_status": "Status", "employee_number": "Employee ID"}

    def __init__(self, *args, can_edit_pay=False, can_link_account=False, can_edit_personal=True, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = user
        if user is not None:
            for field in self.fields.values():
                if hasattr(field, "queryset") and field.queryset is not None:
                    field.queryset = scope_model_queryset(field.queryset, user)
        self.fields["organization"].required = False
        if self.instance.pk:
            self.fields["organization"].disabled = True
        # Restricted fields are removed entirely, so a crafted POST cannot set them either.
        if not can_edit_pay:
            del self.fields["basic_salary"]
        if not can_edit_personal:
            for name in ("preferred_name", "date_of_birth", "address", "emergency_contact_name", "emergency_contact_phone"):
                del self.fields[name]
        if not can_link_account:
            del self.fields["user"]
        else:
            # Only active, not-yet-linked accounts (plus this employee's current one).
            current = self.instance.user_id
            self.fields["user"].queryset = (
                get_user_model().objects.filter(is_active=True)
                .filter(Q(employee_profile__isnull=True) | Q(pk=current)).order_by("username")
            )
            self.fields["user"].required = False

        # Separation (resigned, terminated...) goes through the Deactivate action so the
        # login is revoked too; this form only toggles probation <-> active.
        if self.instance.pk and self.instance.employment_status not in Employee.ACTIVE_STATUSES:
            del self.fields["employment_status"]
        else:
            self.fields["employment_status"].choices = [
                c for c in Employee.STATUS if c[0] in Employee.ACTIVE_STATUSES]

        managers = Employee.objects.filter(is_active=True)
        if self.instance.pk:
            managers = managers.exclude(pk=self.instance.pk)
        self.fields["manager"].queryset = scope_model_queryset(managers, user) if user is not None else managers
        for name in ("company", "branch", "department", "team", "position", "job_grade", "cost_center", "employment_type", "work_location", "work_schedule"):
            self.fields[name].queryset = self.fields[name].queryset.filter(
                **({"is_active": True} if hasattr(self.fields[name].queryset.model, "is_active") else {}))

        if user is not None:
            for field in self.fields.values():
                if hasattr(field, "queryset") and field.queryset is not None:
                    field.queryset = scope_model_queryset(field.queryset, user)
            if "user" in self.fields and not user.is_superuser:
                from apps.organizations.models import Organization
                if not user.is_superuser:
                    from apps.organizations.tenancy import organization_ids_for
                    self.fields["user"].queryset = self.fields["user"].queryset.filter(
                        organization_memberships__organization_id__in=organization_ids_for(user),
                        organization_memberships__is_active=True).distinct()

    def clean_organization(self):
        organization = self.cleaned_data.get("organization")
        if self.instance.pk:
            return self.instance.organization
        if organization is not None or self.actor is None:
            return organization
        choices = list(self.fields["organization"].queryset[:2])
        if len(choices) == 1:
            return choices[0]
        raise forms.ValidationError("Select an organization.")


class EmployeeSelfForm(forms.ModelForm):
    """The only fields an employee may change on their own record."""

    class Meta:
        model = Employee
        fields = ["email", "phone", "address", "emergency_contact_name", "emergency_contact_phone"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}


class CorrectionRequestForm(forms.Form):
    """What an employee asks for. The service applies the real rules; this only collects the values."""

    date = forms.DateField(widget=D)
    status = forms.ChoiceField(choices=Attendance.STATUS, label="Correct status")
    check_in = forms.TimeField(required=False, widget=T)
    check_out = forms.TimeField(required=False, widget=T)
    ends_next_day = forms.BooleanField(required=False, label="Check-out is on the next day (overnight shift)")
    reason = forms.CharField(max_length=255, help_text="What went wrong? (seen by your manager / HR)")


class BulkAttendanceForm(forms.Form):
    date = forms.DateField(widget=D)
    status = forms.ChoiceField(choices=Attendance.STATUS, label="Mark as")
    check_in = forms.TimeField(required=False, widget=T)
    check_out = forms.TimeField(required=False, widget=T)
    ends_next_day = forms.BooleanField(required=False)
    reason = forms.CharField(max_length=255)
    overwrite = forms.BooleanField(required=False, label="Replace existing records")


class DeactivateEmployeeForm(forms.Form):
    status = forms.ChoiceField(choices=[c for c in Employee.STATUS if c[0] not in Employee.ACTIVE_STATUSES])
    separation_date = forms.DateField(required=False, widget=D, initial=date.today,
                                      help_text="Required for resigned, terminated or retired")
    reason = forms.CharField(required=False, max_length=255)


class AttendanceForm(forms.ModelForm):
    """Manual entry / correction by HR. A reason is mandatory and goes into the audit history."""

    reason = forms.CharField(max_length=255, help_text="Why is this entry being added or corrected? (kept in the history)")

    class Meta:
        model = Attendance
        fields = ["emp", "date", "status", "check_in", "check_out", "ends_next_day"]
        widgets = {"date": D, "check_in": T, "check_out": T}
        labels = {"ends_next_day": "Check-out is on the next day (overnight shift)"}
        labels = {"emp": "Employee"}


class LeaveApplicationForm(forms.ModelForm):
    class Meta:
        model = LeaveApplication
        fields = ["leave_type", "start_date", "end_date", "reason"]
        widgets = {
            "start_date": D,
            "end_date": D,
            "reason": forms.Textarea(attrs={"rows": 3, "placeholder": "Please provide a clear reason for your leave request..."}),
        }

    def __init__(self, *args, **kwargs):
        self.employee = kwargs.pop("employee", None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        # The rules live in services.leave so they are shared and testable on their own.
        validate_leave_request(self.employee, cleaned.get("leave_type"),
                               cleaned.get("start_date"), cleaned.get("end_date"))
        return cleaned
