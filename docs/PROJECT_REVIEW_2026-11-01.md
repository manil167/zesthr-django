# HROS project review — 1 November 2026

## Scope and outcome

Reviewed the current Django application and organization models, admin registrations/forms, request selectors, attendance and employee writes, leave/correction review, payroll lifecycle/exports, documents, anomaly review, notifications, WhatsApp, read-only REST API, authentication integration, deployment settings and existing regression coverage. This is a code and targeted production-integrity review, not an independent penetration test or a promise that every future change is isolated automatically.

The application uses explicit active organization membership and role permissions. Platform superusers intentionally have cross-organization access. Tenant-owned records are selected through `scope_queryset` / `scope_model_queryset`; self-service screens additionally select the authenticated employee. Database triggers prevent tenant ownership transfers and invalid cross-tenant relationships. These triggers are relationship guards, not PostgreSQL row-level security; read authorization remains in Django.

## Corrections made

- Restricted the shared Sites and legacy allauth EmailAddress/SocialApp/SocialAccount/SocialToken admin sections to active platform superusers, even if a tenant operator has their model permissions. Those shared identity/configuration records had no tenant boundary and must not be exposed as tenant-owned admin sections. Group administration and organization membership administration already have platform-only controls.
- Added tenant checks inside attendance writes and permission plus tenant checks inside employee deactivate/reactivate services when an actor is provided. The HTTP views already checked access; the service checks also protect future callers.
- Required an explicit user on the unused employee-summary and pending-leave aggregate helpers. Scoped the attendance register’s intermediate attendance query, and scoped manager-report selectors instead of relying only on their final employee relationship.
- Added early model validation for cross-organization department schedules and job-position departments, in addition to existing database guards. Manager validation now terminates when imported data contains a reporting cycle.
- Replaced generated Neon identifiers in account selectors with first/last name and email. Sorting is by name/email, and dropdown querysets retain their tenant restrictions. Unnamed Neon accounts use an account-number/email fallback. Internal usernames, account IDs, permission assignments and identity links are unchanged. Audit/profile UI labels and compliance actor exports use readable account labels.
- Corrected a duplicate form-label declaration that discarded the overnight-attendance help label.
- Made the PostgreSQL pooling configuration test independent of local environment overrides.
- Added the active-staff administrative handbook at `/admin/guide/`, linked from the admin header and signed-in navigation. Its section/field reference follows the actual reader’s admin model permissions; it never reads HR records or credential values to generate examples.
- Fixed mobile homepage grid minimum widths, a clipped navigation row, attendance-table scrolling and deduction-card wrapping. At a 390-pixel browser viewport, the table is constrained to its card, horizontal keyboard scrolling changes its scroll offset, and deduction rows use a vertical layout.

## Tenant boundary coverage

| Surface | Boundary checked |
| --- | --- |
| Organizations, companies and reference data | Active membership; parent ownership; immutable existing tenant ownership; scoped admin related fields |
| Employee directory, profiles and login linking | Scoped querysets and choices; personal/pay permissions; self/manager rules; employee relationship validation |
| Attendance and corrections | Scoped register/history/detail/edit requests; employee-derived self-service identity; scoped review queues; actor checks on write services |
| Leave and balances | Employee-owned entitlements/requests; scoped independent decisions; locked balance writes |
| Payroll and reports | Scoped employee/run selectors; organization-filtered snapshots; independent approval/finalization; tenant-filtered CSV/API results |
| Documents and retention | Employee scope and personal-data/edit permission before access; private storage; user-requested purges authorized before trusted retention processing |
| Anomalies | Scoped scan/review queues; explained decisions; no automatic disciplinary outcomes |
| Notifications and announcements | Recipient plus active organization scope; membership checked on announcements/invitations and WhatsApp employee actions |
| REST API | Authenticated read-only viewsets, tenant filtering on list/detail, role/self-service restrictions |
| Admin auth/provider configuration | Shared platform configuration restricted to active superusers; tenant staff cannot use model permission grants to open it |
| Scheduled/background work | Trusted management callers can intentionally operate across tenants; they are deployment/operator interfaces, not public request endpoints |

Services retaining `user=None` / `by=None` are trusted internal/management entry points. Public request paths must pass their authenticated actor or resolve an employee through the authorization helpers. New public callers must not invoke the trusted global mode. Shared login identities can belong to multiple organizations; account-security events are identity-scoped rather than separate per-organization audit events.

## Verification

Regression tests cover guide access, permission-filtered fields and navigation, readable/scoped dropdowns, denied cross-tenant service writes, model relationship validation, inactive membership revocation, and shared-provider admin denial. Existing suites exercise cross-tenant lists/details/POSTs, exports/APIs, PostgreSQL bulk-SQL guards, authorization, payroll review, private documents and concurrent operations. Run the complete test matrix on SQLite and PostgreSQL, lint, migration-drift checks and the production asset/container builds before accepting a release.

Read-only checks against the configured HROS production Neon branch found zero mismatches for employee schedules, department schedules, position departments and employee payroll-component organizations, and zero disabled tenant-guard triggers. These were aggregate checks: no employee/document/payroll contents or credentials were downloaded. They are targeted checks, not an exhaustive dump or historical audit.

## Recommended next improvements

1. **Per-organization roles if required:** current permissions/groups belong to the user; memberships determine which organizations those permissions can act in. If a person must be HR in organization A but read-only in B, add membership-level roles and enforce them through one authorization service. Do not assume current global groups provide this distinction.
2. **User-selection usability at larger scale:** keep name/email labels and add a tenant-filtered search/autocomplete when account counts justify it. Autocomplete endpoints must retain the same role and tenant checks as the forms.
3. **Operational verification:** schedule restore drills, private-document storage/retention checks, failed-worker alerts and AI cost/availability monitoring. A passing application test does not verify an external storage account’s live IAM/KMS policy or a successful restore.
4. **Reduce legacy surface:** keep unused legacy provider admin sections platform-only; consider removing them once the business confirms that only Neon Auth is used. Remove compatibility global selector modes after migrating their trusted management callers explicitly.
5. **Keep boundary tests mandatory:** retain PostgreSQL CI, including raw/bulk SQL and concurrent tests, for every change touching identity, memberships, reference relationships, reporting, payroll or documents.

No production permissions were granted, tenant memberships changed, employee records rewritten, or credentials rotated as part of this review.
