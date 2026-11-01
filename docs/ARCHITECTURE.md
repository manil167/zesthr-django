# HROS product architecture

HROS is an employee-lifecycle platform (HRMS/HRIS), not only an attendance and
payroll application. It is developed as a modular Django monolith: modules own
their data and business rules while sharing one deployable application until a
separate service is justified by a concrete operational need.


## Implementation status

The domain table and global-readiness sections below describe the architectural roadmap. Current business models and most workflows remain in core, with modular services under core/services, explicit selectors and permissions, and organization ownership under apps/organizations. Document and anomaly models have separate modules. Recruitment, talent, AI, benefits, and comprehensive branch/country policy modules are not shipped implementations. See PROGRESS.md for verified delivery and outstanding operational work. This distinction also applies to the target module ownership and hire-to-active workflow descriptions below.

## Product domains

| Domain | Responsibility |
|---|---|
| Identity and access | Accounts, authentication, roles, sessions and security events. |
| Organization | Tenant, company, branch, department, team, location and policy scope. |
| Employees | Employee 360 record, employment lifecycle and employment history. |
| Talent | Recruitment, onboarding, performance, goals, learning and engagement. |
| Workforce | Attendance, schedules, leave and workforce exceptions. |
| Reward | Payroll, compensation, benefits and expenses. |
| Employee services | Documents, HR requests, announcements and notifications. |
| Platform | Reporting, audit, integrations, automation, API and AI-assisted features. |

## Module boundaries

The current `core` application remains the compatibility boundary for the
working attendance system. New work is introduced under `apps/` by domain, in
this order: `accounts`, `organizations`, `employees`, `attendance`,
`scheduling`, `leave`, and `payroll`; then the remaining roadmap modules.

Each module owns its models, migrations, services, selectors, permissions and
URLs. Views and API endpoints only orchestrate forms/serializers, permissions
and services. A module must not write another module's models directly; it
calls that module's published service or emits a domain event instead.

## Core workflows

| Workflow | System of record | Outcome |
|---|---|---|
| Hire to active employee | Employees + onboarding | Account, assignment and onboarding tasks are provisioned. |
| Work day | Attendance + scheduling | Punches are evaluated against an assigned schedule; exceptions are reviewable. |
| Leave request | Leave + attendance | An approved request updates availability without silently discarding history. |
| Payroll | Payroll + attendance | A reviewed calculation becomes an immutable finalized payroll result and payslip. |
| Exit | Employees + offboarding | Access is revoked while HR, attendance, leave and payroll history is retained. |

## Experiences and access scope

- Employees use self-service for their own profile, attendance, leave, documents and payslips.
- Managers see only their reporting scope and approve assigned actions.
- HR administers employee records, policies, workflows and operational exceptions.
- Payroll and finance see compensation information only when explicitly authorized.
- Administrators configure organizations, integrations and global controls.

Every business record will carry organization ownership before it is exposed to
more than one organization. Querysets and services must apply that scope by
default; object-level checks are defense in depth, not the primary isolation
mechanism.

## Cross-cutting standards

- Prefer configuration and effective-dated history over hard-coded policy rules.
- Keep money in `Decimal`, store timestamps in UTC and display them in the
  organization's configured time zone.
- Preserve historical HR records; lifecycle changes are auditable events, not
  destructive overwrites.
- Protect sensitive personal, pay, tax and document data with least privilege
  and audited access.
- Build service-level tests for rules and permission/isolation tests for every
  new protected workflow.
- Version public APIs under `/api/v1/`; web and mobile clients share the same
  services and authorization rules.

## Scale and global readiness

HROS targets PostgreSQL in production, tenant-aware data access, country- and
organization-scoped configuration, localization, time zones and multiple
currencies. Background processing, caching and service extraction are added
only when measured workload requires them. The product must not make automated
employment decisions; AI output is advisory and subject to authorized human
review.

## Incremental migration

Existing `core` routes, models and permissions remain operational during the
migration. New domain services are extracted behind compatibility adapters,
with tests proving unchanged behavior before a legacy path is removed. Each
change is independently reviewable, migratable and reversible.
