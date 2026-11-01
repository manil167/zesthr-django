"""Cross-table tenant invariants also apply to bulk SQL and administrative updates.

These are relationship guards, not PostgreSQL RLS: request authorization remains
in Django and the database credential must never be exposed to a browser.
"""
from django.db import migrations


OWNERS = {
    'organizations.Organization': 'id',
    'organizations.Company': 'organization', 'organizations.Branch': 'company__organization',
    'organizations.Division': 'company__organization', 'organizations.Team': 'division__company__organization',
    'organizations.JobGrade': 'company__organization', 'organizations.CostCenter': 'company__organization',
    'core.Department': 'organization', 'core.JobPosition': 'organization',
    'core.EmploymentType': 'organization', 'core.WorkLocation': 'organization',
    'core.WorkSchedule': 'organization', 'core.Employee': 'organization',
    'core.Holiday': 'organization', 'core.HolidayCalendar': 'organization',
    'core.PayrollComponent': 'organization', 'core.PayrollRun': 'organization',
    'core.Attendance': 'emp__organization', 'core.Salary': 'emp__organization',
    'core.LeaveApplication': 'emp__organization', 'core.AttendanceCorrectionRequest': 'emp__organization',
    'core.ScheduledEmploymentChange': 'employee__organization',
    'core.EmployeePayrollComponent': 'employee__organization',
    'core.PayrollAdjustment': 'employee__organization', 'core.LeaveEntitlement': 'employee__organization',
    'core.EmployeeDocument': 'employee__organization', 'core.AttendanceAnomaly': 'employee__organization',
    'core.Announcement': 'organization', 'core.OrganizationInvitation': 'organization',
    'core.CalendarHoliday': 'calendar__organization',
}


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor not in ('sqlite', 'postgresql'):
        raise RuntimeError('Tenant integrity guards require SQLite or PostgreSQL.')
    q = schema_editor.quote_name

    def value(model, path, row='NEW'):
        field_name, *rest = path.split('__')
        field = model._meta.get_field(field_name)
        column = f'{row}.{q(field.column)}'
        if not rest:
            return column
        related = field.remote_field.model
        nested = value(related, '__'.join(rest), 'related_row')
        # Nested subqueries use distinct aliases to avoid correlation shadowing.
        nested = nested.replace('related_row', 'r_' + related._meta.model_name)
        alias = 'r_' + related._meta.model_name
        return f'(SELECT {nested} FROM {q(related._meta.db_table)} {alias} WHERE {alias}.{q(related._meta.pk.column)} = {column})'

    for label, path in OWNERS.items():
        if label == 'organizations.Organization':
            continue
        model = apps.get_model(label)
        expected = value(model, path)
        checks = []
        # Any two tenant-owned relations on the row must resolve to the same tenant.
        for field in model._meta.fields:
            if not field.is_relation or field.related_model._meta.label not in OWNERS:
                continue
            related_path = OWNERS[field.related_model._meta.label]
            candidate = value(model, field.name + '__' + related_path)
            checks.append(f'({candidate} IS NOT NULL AND {expected} IS NOT NULL AND {candidate} <> {expected})')
        if label == 'core.Employee':
            for field in ('branch', 'job_grade', 'cost_center'):
                checks.append(f'(NEW.company_id IS NOT NULL AND {value(model, field + "__company")} IS NOT NULL AND {value(model, field + "__company")} <> NEW.company_id)')
            checks.append(f'(NEW.company_id IS NOT NULL AND {value(model, "team__division__company")} IS NOT NULL AND {value(model, "team__division__company")} <> NEW.company_id)')
        invalid = ' OR '.join(checks) or '1=0'
        old_owner = value(model, path, 'OLD')
        immutable = f'({old_owner} IS NOT NULL AND ({expected} IS NULL OR {old_owner} <> {expected}))'
        table = q(model._meta.db_table)
        stem = 'tenant_guard_' + model._meta.model_name
        if vendor == 'sqlite':
            for event in ('INSERT', 'UPDATE'):
                condition = f'({invalid})' + (f' OR {immutable}' if event == 'UPDATE' else '')
                schema_editor.execute(f'CREATE TRIGGER {q(stem + "_" + event.lower())} BEFORE {event} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, \'Tenant relationship violation\'); END;')
        else:
            function = q(stem + '_fn')
            schema_editor.execute(f'''CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF {invalid} THEN RAISE EXCEPTION 'Tenant relationship violation' USING ERRCODE = '23514'; END IF;
                    IF TG_OP = 'UPDATE' THEN
                        IF {immutable} THEN RAISE EXCEPTION 'Tenant ownership is immutable' USING ERRCODE = '23514'; END IF;
                    END IF;
                    RETURN NEW;
                END; $$''')
            schema_editor.execute(f'CREATE TRIGGER {q(stem)} BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()')


def uninstall(apps, schema_editor):
    q = schema_editor.quote_name
    for label in OWNERS:
        if label == 'organizations.Organization':
            continue
        model = apps.get_model(label)
        stem = 'tenant_guard_' + model._meta.model_name
        if schema_editor.connection.vendor == 'sqlite':
            for event in ('insert', 'update'):
                schema_editor.execute(f'DROP TRIGGER IF EXISTS {q(stem + "_" + event)}')
        elif schema_editor.connection.vendor == 'postgresql':
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {q(stem)} ON {q(model._meta.db_table)}')
            schema_editor.execute(f'DROP FUNCTION IF EXISTS {q(stem + "_fn")}()')


class Migration(migrations.Migration):
    dependencies = [('core', '0032_tenant_reference_data')]
    operations = [migrations.RunPython(install, uninstall)]
