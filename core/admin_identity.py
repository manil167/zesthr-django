"""Shared auth-provider configuration is platform-owned, never tenant-owned."""
from django.apps import apps
from django.contrib import admin
from core.account_labels import label_user_choices


class PlatformIdentityAdminMixin:
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if self.has_module_permission(request) else queryset.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        return label_user_choices(super().formfield_for_foreignkey(db_field, request, **kwargs))


def register_platform_identity_admins():
    for label in ('sites.Site', 'account.EmailAddress', 'socialaccount.SocialApp',
                  'socialaccount.SocialAccount', 'socialaccount.SocialToken'):
        model = apps.get_model(label)
        current = admin.site.get_model_admin(model)
        admin.site.unregister(model)
        secured = type(f'Platform{type(current).__name__}', (PlatformIdentityAdminMixin, type(current)), {})
        admin.site.register(model, secured)
