"""Tenant-scope primitives used by domain selectors and services."""
from .models import Organization, OrganizationMembership


def organization_ids_for(user):
    if not getattr(user, "is_authenticated", False):
        return Organization.objects.none().values_list("id", flat=True)
    cached = getattr(user, '_hros_request_organization_ids', None)
    if cached is not None:
        return cached
    if not user.is_active:
        return []
    if user.is_superuser:
        return Organization.objects.values_list("id", flat=True)
    return list(OrganizationMembership.objects.filter(
        user=user, is_active=True, organization__is_active=True
    ).values_list("organization_id", flat=True))


def scope_queryset(queryset, user, organization_field="organization"):
    """Restrict an organization-owned queryset to the request user's tenant scope."""
    if getattr(user, "is_superuser", False) and getattr(user, "is_active", False):
        return queryset
    return queryset.filter(**{f"{organization_field}__in": organization_ids_for(user)})


def scope_model_queryset(queryset, user):
    """Explicit ownership paths for models presented in forms and administration."""
    if getattr(user, 'is_superuser', False) and getattr(user, 'is_active', False):
        return queryset
    paths = {
        "securityevent": "user__organization_memberships__organization",
        "announcement": "organization", "organizationinvitation": "organization",
        "organization": "pk", "organizationmembership": "organization",
        "company": "organization", "branch": "company__organization",
        "division": "company__organization", "team": "division__company__organization",
        "jobgrade": "company__organization", "costcenter": "company__organization",
        "employee": "organization", "department": "organization",
        "jobposition": "organization",
        "employmenttype": "organization", "worklocation": "organization",
        "workschedule": "organization", "holiday": "organization",
        "holidaycalendar": "organization", "calendarholiday": "calendar__organization",
        "scheduledemploymentchange": "employee__organization",
        "employmenthistory": "employee__organization",
        "employeedocument": "employee__organization", "documentevent": "document__employee__organization",
        "attendanceanomaly": "employee__organization", "anomalydecision": "anomaly__employee__organization",
        "attendancecorrectionrequest": "emp__organization", "leavedecision": "leave__emp__organization",
        "attendance": "emp__organization", "attendanceaudit": "emp__organization",
        "leaveentitlement": "employee__organization",
        "leavebalanceadjustment": "entitlement__employee__organization",
        "payrollcomponent": "organization", "employeepayrollcomponent": "employee__organization",
        "payrolladjustment": "employee__organization", "payrollrun": "organization", "payrollevent": "run__organization",
        "salary": "emp__organization", "leaveapplication": "emp__organization",
    }
    path = paths.get(queryset.model._meta.model_name)
    if path is None:
        if queryset.model._meta.label_lower == "auth.user":
            return queryset.filter(organization_memberships__organization_id__in=organization_ids_for(user),
                                    organization_memberships__is_active=True).distinct()
        return queryset.none()  # unknown ownership must never silently become global
    return scope_queryset(queryset, user, path)
