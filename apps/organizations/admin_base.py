from django.contrib import admin
from .tenancy import scope_model_queryset


class TenantAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return scope_model_queryset(super().get_queryset(request), request.user)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        kwargs["queryset"] = scope_model_queryset(db_field.remote_field.model.objects.all(), request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)



    def get_list_filter(self, request):
        filters = super().get_list_filter(request)
        scoped = []
        for item in filters:
            if isinstance(item, str) and '__' not in item:
                field = self.model._meta.get_field(item)
                if field.is_relation:
                    item = (item, admin.RelatedOnlyFieldListFilter)
            scoped.append(item)
        return scoped

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields += [name for name in ('organization', 'company', 'division')
                       if any(f.name == name for f in self.model._meta.fields)]
        return fields

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        class ScopedForm(base):
            def clean(form):
                data = super().clean()
                org = data.get('organization') or getattr(form.instance, 'organization', None)
                department = data.get('department')
                schedule = data.get('work_schedule')
                if org and department and department.organization_id != org.pk:
                    form.add_error('department', 'Select a department in the same organization.')
                if org and schedule and schedule.organization_id != org.pk:
                    form.add_error('work_schedule', 'Select a schedule in the same organization.')
                return data
        return ScopedForm
