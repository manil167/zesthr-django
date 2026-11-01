"""Permission-aware help for the administrative site; never reads business records."""
from django.contrib import admin
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.shortcuts import render
from django.utils.text import capfirst
from django.contrib.admin.utils import flatten
from django.views.decorators.http import require_safe


# Explain the workflow, rather than treating every admin form as an unrestricted editor.
SECTIONS = {
    'site': ('The deployment domain used by the legacy authentication integration.', 'Platform configuration: use the exact production domain zesthr-django.onrender.com without a scheme or path, and display name HROS. Do not change it for an employee or organization; it is shared deployment configuration.'),
    'emailaddress': ('Legacy Django/allauth email records, separate from Neon Auth verification.', 'Inspect the account, email and primary/verified flags only when diagnosing the legacy authentication integration. Do not tick Verified to bypass Neon email verification; use the provider-backed HROS verification flow. A record here does not create a Neon identity.'),
    'socialapp': ('Legacy OAuth provider application configuration, shared by the deployment.', 'Platform administrators only: select the provider actually configured for legacy sign-in, enter its registered client ID and secret from the approved secret-management process, and select the correct Site. Do not invent credentials, paste them into support messages, or use this form to configure Neon-managed OAuth.'),
    'socialaccount': ('Legacy social-provider account links.', 'Inspect provider, user and provider UID when diagnosing a legacy OAuth account. Never manually link identities based on a matching email or display name. Neon identity links are separate and are established by the verified sign-in workflow.'),
    'socialtoken': ('Legacy provider token records, containing credentials.', 'Treat this as sensitive platform configuration. Do not create or replace tokens by guesswork or copy them into tickets. Use the authorized provider login/revocation flow and the approved secret-management process. This is not a Neon password or API-key configuration screen.'),
    'organization': ('The top-level tenant. Records and access are separated by organization.',
        'Create Example Services with code EXAMPLE, default time zone Africa/Lagos and currency NGN. Use the currency approved for your company. Only activate organizations that should operate.'),
    'organizationmembership': ('Connects a login account to an organization. A role group alone does not establish tenant membership.',
        'Select the exact verified user, select Example Services, and tick Is active. This establishes organization scope; it does not grant staff status, superuser status or a role. Deactivate the membership to remove that scope.'),
    'company': ('A legal or operating company inside an organization.',
        'Select Example Services; enter Example Services Nigeria, code EX_NG, country NG, and enable Is active. Country uses a two-letter code.'),
    'branch': ('An office reference belonging to a company.',
        'Select Example Services Nigeria; enter Lagos Office, code LAGOS, city Lagos and time zone Africa/Lagos. A branch reference does not by itself configure attendance rules or holidays; configure the relevant work locations and schedules separately.'),
    'division': ('A business division within a company.', 'Select the company; enter Operations, code OPS and Is active. Create divisions before their teams.'),
    'team': ('A team belonging to a division.', 'Select Operations as the division; enter Customer Support, code SUPPORT and Is active.'),
    'jobgrade': ('A company-level grade reference.', 'Select the company; enter Senior Officer, code G05 and Is active. A grade does not automatically change employee pay.'),
    'costcenter': ('A company-level cost reporting reference.', 'Select the company; enter Customer Operations, code CC_SUPPORT and Is active. This reference does not automatically assign payroll components.'),
    'department': ('An employee department and optional default work schedule.', 'Select the organization; enter Finance, code FIN, choose the weekday work schedule if appropriate, and leave Is active selected. Use the same organization as its employees.'),
    'jobposition': ('A job title, optionally associated with a department.', 'Select the organization, enter Payroll Analyst as Title, select Finance as Department, and enable Is active.'),
    'employmenttype': ('An employment category used on employee records.', 'Select the organization and enter Full time, Part time or Contract according to your approved categories.'),
    'worklocation': ('The work location used by employee attendance configuration.', 'Select the organization; enter Lagos Office, city Lagos, country Nigeria and time zone Africa/Lagos. Leave the time zone blank only when the deployment default is appropriate.'),
    'workschedule': ('Defines expected shift times and attendance tolerances.', 'Enter Weekdays 09–17, start 09:00, end 17:00, break 60 minutes, arrival grace 10 minutes and early-leave grace 5 minutes. Select overtime eligibility only when approved by company policy. An end time earlier than the start, such as 22:00–06:00, represents an overnight schedule. These are examples, not default company policy.'),
    'employee': ('The HR employment record, separate from its login account.', 'Select the correct organization and the verified login account only when authorized to link accounts. Enter employee number EMP-001, name Ada Okafor, designation Payroll Analyst, company-approved monthly basic salary, work email and join date. Select Finance, Payroll Analyst, Full time, Lagos Office and the correct schedule from the same organization. Select a manager who is a different employee in that organization. Keep personal and emergency contacts accurate. Use the employee page’s Deactivate/Reactivate actions for separation; the lifecycle fields here are locked.'),
    'scheduledemploymentchange': ('A future-dated transfer or promotion; saving does not apply it immediately.', 'Select Ada Okafor, effective date 2026-12-01, reason Approved transfer to Finance, and at least one new designation, department or manager. Use Employment changes in the main application to apply a due change. Applied at is automatic; applied changes cannot be edited or deleted here.'),
    'holiday': ('A dated holiday reference used by the application.', 'Select the correct organization, enter New Year holiday, date 2027-01-01, choose the applicable Holiday type from the dropdown, and describe its scope. Only enter dates approved for that organization.'),
    'user': ('A login identity. Staff status, roles, organization membership and an employee link serve different purposes.', 'For an existing account, confirm its email and first/last name. Active permits account use; Staff status permits entry to this admin site; Groups grant role permissions; Superuser grants global access and is reserved for platform administrators. With Neon Auth enabled, users register and verify through the HROS signup flow: creating a Django user or changing its local password here does not create/reset a Neon identity. Use the password-reset flow for Neon passwords. Internal neon_ usernames are identity keys; do not replace them with a name. Select only reviewed accounts for Activate/Deactivate actions. Deactivation preserves history; never disable your own account.'),
    'group': ('A reusable set of permissions maintained by platform administrators.', 'Select an existing role such as HR Officer for an HR operator or Payroll Officer for a payroll operator on the User form. Inspect its permissions before assigning it. Role names do not grant access by themselves: permissions, active membership and staff status still matter. Avoid changing a shared group to solve one person’s access problem; it affects every member.'),
    'attendance': ('Read-only attendance records in this panel.', 'Filter by date and status: P = Present, A = Absent, H = Half day, L = Leave. Use Attendance → Daily register, manual entry or Correction requests in the main application for changes, with an explanation. Do not try to bypass the audit workflow here.'),
    'attendanceaudit': ('Protected history of attendance changes.', 'Filter by action/date and search employee name or reason. Compare old and new values, actor and explanation. History is read-only; correct the underlying record through an authorized workflow, rather than editing the evidence.'),
    'securityevent': ('Read-only sign-in and account-security history.', 'Filter the event type and date; search the account identifier or IP address. Review events in context. A failed sign-in is a review signal, not proof of misconduct. Entries cannot be added, edited or deleted here.'),
    'leavedecision': ('Read-only evidence of leave approvals or rejections.', 'Inspect the leave request, status, decision maker, note and time. Approve or reject requests through Leaves in the main application; this section does not offer a shortcut to approve them.'),
    'leaveentitlement': ('The annual allowance and accrual rules for one employee and leave type.', 'Select Ada Okafor, the relevant leave type, year 2027, annual days 20.00 and carryover days 2.00. Tick Monthly accrual only when allowance is earned over the year; tick Working days only only if weekends/holidays should be excluded by policy. Set Monthly limit only when the policy imposes one; leave it blank otherwise. Do not duplicate an employee/type/year combination. Check the balance screen after saving.'),
    'leavebalanceadjustment': ('An append-only correction to a leave entitlement.', 'Select the employee’s entitlement; enter 2.00 to add two days or -1.00 to subtract one day. Enter a specific reason, such as Approved carryover correction, reference HR-104. Zero is invalid. Author/time are automatic. Reverse a mistake with a new explained adjustment rather than editing history.'),
    'payrollcomponent': ('Defines a reusable earning or deduction category.', 'Select the organization; enter Transport allowance, code TRANSPORT, Component type Earning, Active selected and a description. Taxable is classification metadata, not an automatic legal tax calculation. Use Deduction for a deduction category; amounts on assignments remain positive.'),
    'employeepayrollcomponent': ('Assigns a recurring earning/deduction to an employee.', 'Select Ada Okafor and Transport allowance in the same organization. Enter amount 15000.00, Effective from 2026-12-01, optional Effective to, and Active. Enter a positive amount; the component type determines whether it adds or deducts. End dates must not precede start dates. Avoid unintended overlapping assignments.'),
    'payrolladjustment': ('A one-period earning or deduction used by payroll.', 'Select employee, year 2026, month 12, description Approved December bonus, amount 25000.00 and Component type Earning. Mark Approved only after the required business review. For a deduction choose Deduction and a positive amount. Created by/time are automatic. Check the preview before payroll approval; a saved adjustment does not rewrite finalized payroll.'),
    'salary': ('Read-only saved employee salary results.', 'Review the employee, year/month and totals. Generate and review results through Payroll in the main application. Do not edit saved salary records directly.'),
    'payrollrun': ('Read-only payroll-run status and snapshot history.', 'Use Payroll runs in the main application to preview, submit, approve independently, finalize or reverse as your permissions permit. Review attendance, leave, components and adjustments before submission. A finalized run must follow the reversal workflow; it is not silently recalculated.'),
    'payrollevent': ('Read-only payroll operation history.', 'Review the linked run, action, actor, reason and timestamp to understand who submitted, approved, finalized or reversed it. Entries cannot be edited or deleted here.'),
}

FIELD_EXAMPLES = {
    'name': 'Example: Finance or Lagos Office; use the official name for this record.',
    'code': 'Example: FIN. Use a stable, unique short code within the parent scope. Organization structure codes use uppercase letters, digits, underscores or hyphens.',
    'organization': 'Select the organization that owns this record. All related selections must belong to the same organization.',
    'company': 'Select the company that owns this reference.', 'division': 'Select the parent division, not an unrelated company’s division.',
    'user': 'Select the exact verified login account. Check its email before linking; never choose an account just because its name looks similar.',
    'employee': 'Select the intended employee from your authorized organization.', 'emp': 'Select the intended employee.',
    'manager': 'Select a different employee in the same organization. Never select the employee as their own manager.',
    'department': 'Select the department in the same organization.', 'position': 'Select the employee’s actual job position.',
    'work_schedule': 'Select the approved shift schedule; a department default may apply when no employee override is selected.',
    'time_zone': 'Example: Africa/Lagos. Use an IANA time-zone name, not a UTC offset.',
    'default_time_zone': 'Example: Africa/Lagos. Use the organization’s approved IANA time zone.',
    'default_currency': 'Example: NGN. Use the approved three-letter currency code.',
    'basic_salary': 'Example: 250000.00 for the agreed monthly basic salary, without currency symbols or thousands separators.',
    'employee_number': 'Example: EMP-001. Use the organization’s unique employee identifier.',
    'first_name': 'Example: Ada. Enter the given name.', 'last_name': 'Example: Okafor. Enter the family name.',
    'email': 'Example: ada@example.com. Use the correct work/login address for this field.',
    'phone': 'Example: +2348000000000; replace with a real authorized contact number.',
    'reason': 'Explain the actual change and approval reference; for example Approved transfer, HR-104. Avoid vague notes such as fixed.',
    'description': 'Describe the purpose and applicable period/scope so another authorized reviewer can understand it.',
    'year': 'Example: 2027; choose the applicable leave/payroll year.', 'month': 'Enter 1–12; December is 12.',
    'amount': 'Example: 15000.00, without a currency symbol. Use a non-negative amount; the earning/deduction type determines its effect.',
    'days': 'Example: 2.00 adds two days; -1.00 removes one day. Explain the adjustment; zero is invalid.',
    'annual_days': 'Example: 20.00, only if that is the employee’s approved annual allowance.',
    'carryover_days': 'Example: 2.00, only for approved carryover; otherwise 0.00.',
    'start_time': 'Example: 09:00, using the 24-hour clock.', 'end_time': 'Example: 17:00; a schedule ending earlier than it starts is treated as overnight.',
    'break_minutes': 'Example: 60 minutes, according to the approved schedule.', 'grace_minutes': 'Example: 10 minutes of arrival tolerance; use the company’s policy.',
    'early_leave_grace_minutes': 'Example: 5 minutes, if allowed by policy.',
    'working_days': 'Enter a JSON list of weekday numbers: [0, 1, 2, 3, 4] means Monday through Friday; 5 is Saturday and 6 is Sunday.',
    'is_default': 'Tick for the organization’s fallback schedule. Only one default schedule is allowed per organization.',
    'overtime_after_minutes': 'Example: 30; overtime counts only after the configured threshold is met.',
    'overtime_max_minutes': 'Example: 240 for a four-hour daily limit; 0 means no configured limit.',
    'overtime_multiplier': 'Example: 1.50 if approved policy pays 1.5 times the normal hourly rate.',
    'is_staff': 'Tick only for authorized administrative-panel operators. This alone does not grant model permissions.',
    'is_superuser': 'Platform-wide access: only grant to an explicitly authorized platform administrator.',
    'groups': 'Select approved role groups, for example HR Officer. Review the actual permissions attached to the group.',
    'user_permissions': 'Individual permissions are exceptional; use reviewed role groups where possible.',
    'username': 'An internal account key for Neon accounts; preserve it. Human names belong in First name and Last name.',
    'monthly_accrual': 'Tick only if the annual allowance is earned progressively each month.',
    'working_days_only': 'Tick only if the leave policy excludes non-working days.',
    'monthly_limit': 'Example: 2 days only when a monthly cap applies; leave blank for no configured monthly cap.',
    'approved': 'Tick only after the required review. Unapproved adjustments are not treated as approved payroll input.',
}


def field_guidance(model_admin, request):
    """Use the actual role-filtered admin fields, never database values or choices."""
    # UserAdmin's add screen only collects credentials; document its existing-account
    # screen too, where operators actually edit names, email and access settings.
    obj = model_admin.model() if model_admin.model._meta.label_lower == 'auth.user' else None
    readonly = model_admin.get_readonly_fields(request, obj)
    fields = []
    for name in flatten(model_admin.get_fields(request, obj)):
        try:
            field = model_admin.model._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        if isinstance(field, models.AutoField):
            continue
        automatic = name in readonly or not field.editable
        hint = FIELD_EXAMPLES.get(name)
        if model_admin.model._meta.label_lower == 'auth.user' and name == 'password':
            automatic = True
            hint = 'The stored local password hash is not a plain-text password. Use the authorized password-change/reset flow; Neon credentials are managed by the provider-backed HROS screens.'
        if not hint:
            if field.is_relation:
                hint = f'Select the matching {field.related_model._meta.verbose_name} from the records your role can access.'
            elif isinstance(field, models.BooleanField):
                hint = 'Tick for yes; leave unticked for no. Use the approved business setting.'
            elif isinstance(field, models.DateTimeField):
                hint = 'Use the displayed date/time format and check the applicable time zone.'
            elif isinstance(field, models.DateField):
                hint = 'Example: 2026-12-01. Use the calendar/date format shown on the form.'
            elif isinstance(field, models.DecimalField):
                hint = 'Enter a decimal number without a currency symbol, for example 2.00; use the approved value.'
            elif isinstance(field, models.IntegerField):
                hint = 'Enter a whole number appropriate to this field, for example 1. Respect the validation limits shown on the form.'
            else:
                hint = 'Enter the relevant factual value for this record; follow the example and workflow above.'
        fields.append({'label': capfirst(field.verbose_name), 'hint': hint,
                       'help': field.help_text, 'choices': list(field.flatchoices) if field.choices else [],
                       'requirement': 'Read-only / automatic' if automatic else ('Optional' if field.blank else 'Required')})
    return fields


@require_safe
def admin_guide(request):
    sections = []
    for app in admin.site.get_app_list(request):
        for entry in app['models']:
            model_admin = admin.site.get_model_admin(entry['model'])
            purpose, example = SECTIONS.get(entry['object_name'].lower(), (
                'This section is available according to your role permissions.',
                'Review the form labels, required fields and validation messages before saving.'))
            readonly = not entry['perms'].get('add') and not entry['perms'].get('change')
            fields = field_guidance(model_admin, request)
            if readonly:
                for field in fields:
                    field['requirement'] = 'Read-only for your account'
            sections.append({**entry, 'anchor': entry['model']._meta.label_lower.replace('.', '-'),
                             'purpose': purpose, 'example': example,
                             'fields': fields, 'readonly': readonly})
    return render(request, 'admin/guide.html', {**admin.site.each_context(request),
                  'title': 'Administrative panel guide', 'sections': sections})
