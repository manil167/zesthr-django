from django.contrib import admin

from .admin_base import TenantAdmin
from .models import Branch, Company, CostCenter, Division, JobGrade, Organization, Team


@admin.register(Organization)
class OrganizationAdmin(TenantAdmin):
    list_display = ("name", "code", "default_time_zone", "default_currency", "is_active")
    search_fields = ("name", "code")
    list_filter = ("is_active",)


@admin.register(Company, Branch, Division, Team, JobGrade, CostCenter)
class OrganizationReferenceAdmin(TenantAdmin):
    list_display = ("name", "code", "is_active")
    search_fields = ("name", "code")
    list_filter = ("is_active",)


from .models import OrganizationMembership


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(TenantAdmin):
    list_display = ("user", "organization", "is_active")

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
