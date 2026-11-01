"""Preserve formerly global policies as independent records in each existing tenant."""
from django.db import migrations


def isolate_references(apps, schema_editor):
    Organization = apps.get_model('organizations', 'Organization')
    Employee = apps.get_model('core', 'Employee')
    Department = apps.get_model('core', 'Department')
    default, _ = Organization.objects.get_or_create(code='DEFAULT', defaults={'name': 'Default Organization'})
    organizations = list(Organization.objects.values_list('pk', flat=True))
    copies = {}
    for name in ('WorkSchedule', 'EmploymentType', 'WorkLocation', 'Holiday', 'HolidayCalendar', 'JobPosition'):
        Model = apps.get_model('core', name)
        for item in list(Model.objects.all()):
            # Department ownership determines a legacy position's tenant.
            if name == 'JobPosition' and item.department_id:
                owner = Department.objects.get(pk=item.department_id).organization_id or default.pk
                Model.objects.filter(pk=item.pk).update(organization_id=owner)
                item.organization_id = owner
            for org_id in organizations:
                if org_id == item.organization_id:
                    copies[name, item.pk, org_id] = item.pk
                    continue
                if name == 'JobPosition' and item.department_id:
                    continue
                values = {f.attname: getattr(item, f.attname) for f in Model._meta.concrete_fields
                          if not f.primary_key and f.name != 'organization'}
                values['organization_id'] = org_id
                # Clone global reference records once. Never move the original across tenants.
                clone = Model.objects.create(**values)
                copies[name, item.pk, org_id] = clone.pk
                if name == 'HolidayCalendar':
                    Holiday = apps.get_model('core', 'CalendarHoliday')
                    for holiday in Holiday.objects.filter(calendar_id=item.pk):
                        Holiday.objects.create(calendar_id=clone.pk, name=holiday.name, date=holiday.date,
                            is_working_day=holiday.is_working_day, description=holiday.description)
    for department in Department.objects.exclude(work_schedule_id=None):
        replacement = copies.get(('WorkSchedule', department.work_schedule_id, department.organization_id))
        if replacement:
            Department.objects.filter(pk=department.pk).update(work_schedule_id=replacement)
    fields = {'WorkSchedule': 'work_schedule_id', 'WorkLocation': 'work_location_id',
              'EmploymentType': 'employment_type_id', 'JobPosition': 'position_id'}
    for employee in Employee.objects.all().iterator():
        updates = {}
        for model, field in fields.items():
            current = getattr(employee, field)
            if current:
                replacement = copies.get((model, current, employee.organization_id))
                if replacement:
                    updates[field] = replacement
                else:
                    raise RuntimeError('An employee has a position owned by another organization; correct tenant assignments before migrating.')
        if updates:
            Employee.objects.filter(pk=employee.pk).update(**updates)
    # Backfill notification provenance when a recipient has one unambiguous tenant.
    Membership = apps.get_model('organizations', 'OrganizationMembership')
    Notification = apps.get_model('core', 'Notification')
    for user_id in Notification.objects.filter(organization_id=None).values_list('recipient_id', flat=True).distinct():
        ids = list(Membership.objects.filter(user_id=user_id, is_active=True).values_list('organization_id', flat=True)[:2])
        if len(ids) == 1:
            Notification.objects.filter(recipient_id=user_id, organization_id=None).update(organization_id=ids[0])


class Migration(migrations.Migration):
    dependencies = [('core', '0031_neon_identity')]
    operations = [migrations.RunPython(isolate_references, migrations.RunPython.noop)]
