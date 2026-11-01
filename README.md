# HROS

HROS manages employee records, attendance, leave, reviewed payroll, private employee documents, notifications, and audit reporting. It runs as a Django modular monolith with PostgreSQL in production and SQLite for development.

The current release has automated authorization, tenant-isolation, attendance, leave, payroll, migration, and storage tests. Production readiness still depends on verified deployment settings, private storage provisioning, recovery operations, and approved business policies. See [progress and remaining work](docs/PROGRESS.md).

## Local setup

Use Python 3.12. Create and activate a virtual environment, then run:

```sh
python -m pip install -r requirements/development.txt
```

Copy `.env.example` to `.env`, select development settings, and supply a local secret. Keep secrets out of Git. The development database is SQLite; production settings require PostgreSQL, explicit hosts, a strong secret, and email configuration.

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/. On Windows, activate with `.venv\Scripts\Activate.ps1`; on Unix, use `source .venv/bin/activate` after creating the environment with `python -m venv .venv`. Do not generate new migrations merely to initialize an existing checkout.

`seed_demo` is optional and restricted to debug development environments. Its known-password accounts belong only in disposable local databases. Existing production records must never be replaced by demo data.

## Core workflows

- Provision login accounts in the restricted administration interface, link employees, and assign employment dates, departments, managers, and schedules. Provisioned accounts must change their initial password. Deactivation revokes the linked login and retains history.
- Employees check in/out, request attendance corrections, apply for leave, read notifications, and access their own available payslips and documents.
- Authorized managers review direct-report leave and correction requests. HR access is limited by explicit permissions and organization membership.
- Payroll operators calculate and review snapshots. Independent approval precedes finalization. Finalized payroll is immutable; reversals and subsequent adjustments preserve history.
- Attendance anomalies are reviewable exceptions. They do not establish misconduct or automatically impose penalties.
- PDF documents use private storage, validated uploads, authorized downloads, audit events, and retention controls. Production uploads stay disabled until durable storage is configured.

## Verification

```sh
python manage.py test
python manage.py makemigrations --check --dry-run
python -m pip_audit
```

The default test command uses isolated test settings. GitHub Actions runs the full suite on SQLite and PostgreSQL, checks migration drift and selected lint errors, enforces coverage of at least 75%, audits dependencies, and builds/verifies the non-root container. PostgreSQL concurrency tests intentionally skip on SQLite. Use the isolated Compose service and settings described in [operations](docs/OPERATIONS.md) for local PostgreSQL verification.

## Documentation

- [Architecture and current module boundaries](docs/ARCHITECTURE.md)
- [Accounts and roles](docs/ACCOUNTS.md)
- [Attendance policies](docs/ATTENDANCE_POLICY.md)
- [Leave, payroll, and data ownership policies](docs/BUSINESS_POLICIES.md)
- [Read-only versioned API](docs/API.md)
- [Database migration](docs/DATABASE.md)
- [Deployment, backup, recovery, rollback, and document storage](docs/OPERATIONS.md)
- [Implementation evidence and limitations](docs/PROGRESS.md)

The deployment target is Render using `render.yaml` and `scripts/start_server.py`. The older Vercel material is historical guidance and is not the verified production deployment path. Store production environment values in the hosting secret manager; `.env.example` documents their names without credentials.

Native mobile applications, automatic statutory taxation, and comprehensive country/branch-specific policies are outside the verified release. Existing location and calendar reference models do not establish a complete multi-branch business implementation.

## Neon, organization isolation and messaging

See [Neon Auth and messaging setup](docs/NEON_AND_MESSAGING.md) for Neon PostgreSQL
and managed Neon Auth, organization invitations, tenant isolation, HR announcements,
the website help assistant, and verified WhatsApp attendance and notification commands.
Neon and Meta account configuration is required before their live integrations work.

## Create a testing administrator from Render environment settings

Remove `NEON_AUTH_BASE_URL` from Render to use Django login, then set:

| Variable | Value |
| --- | --- |
| `DJANGO_BOOTSTRAP_ADMIN` | `True` |
| `DJANGO_SUPERUSER_USERNAME` | Your chosen administrator username |
| `DJANGO_SUPERUSER_EMAIL` | Your email address |
| `DJANGO_SUPERUSER_PASSWORD` | A strong unique password, at least 10 characters |

Save and redeploy. Startup migrates the configured database and creates the admin.
Sign in at `/admin/`, then set `DJANGO_BOOTSTRAP_ADMIN=False` and remove
`DJANGO_SUPERUSER_PASSWORD` from Render. Save/redeploy again.
Existing accounts are never promoted, reactivated or given a new password.
This creates a platform superuser with access across organizations; ordinary HR
users should have scoped memberships. Neon Auth uses the verified-user administrator setup below rather than local
passwords. Never commit real credentials.

## Neon Auth administrator and password recovery

Keep `NEON_AUTH_BASE_URL` enabled to let Neon own signup, login, verification and
password recovery. A local Django administrator password is not a Neon password.
Register your account with Neon through `/signup/` and verify its email first.
In Neon Console → Auth → Users, copy that account's exact **user ID**. Set in Render:

| Variable | Value |
| --- | --- |
| `NEON_AUTH_BASE_URL` | Your production branch Auth URL, including `/auth` |
| `NEON_AUTH_APP_ORIGIN` | `https://zesthr-django.onrender.com` |
| `DJANGO_BOOTSTRAP_ADMIN` | `True` temporarily |
| `NEON_ADMIN_USER_ID` | The exact verified Neon user's ID |
| `NEON_ADMIN_EMAIL` | That Neon user's verified email address |

Save/redeploy, then sign in with the **Neon email and Neon password**, and open
`/admin/`. Only the matching verified provider ID and email receive platform admin
access. No local account is automatically linked by email. After successful login,
set `DJANGO_BOOTSTRAP_ADMIN=False` and delete unused `DJANGO_SUPERUSER_PASSWORD`.
The deliberately granted role remains after bootstrap is disabled.

For password reset, enable email/password in Neon Auth and register the application
origin in Neon's trusted domains. Neon—not Django SMTP—sends recovery email in this
mode. Use `/password/reset/` for an account that actually exists in Neon; local-only
accounts will not receive a provider reset. Check Neon email delivery settings,
spam folders and production SMTP configuration if messages do not arrive.
Provider/network/configuration failures now display a recovery-unavailable message
instead of pretending delivery succeeded. Logs contain only the endpoint, HTTP
status and a bounded error classification, never response bodies or reset tokens.

## Conversational assistant

Signed-in users can open HROS Assistant for conversational app guidance, follow-up questions, and session-scoped history. OpenAI credentials remain server-side; employee records are not queried or sent. See [assistant configuration, privacy boundaries, and limits](docs/ASSISTANT.md). Production AI requires a key in the hosting secret manager; without one the widget explicitly labels its deterministic guidance mode.

## Administrative handbook and project review

Authorized active staff can open `/admin/guide/` from the admin header or the main application navigation. The handbook explains setup, permissions, workflows and field examples; its model reference follows the reader’s admin permissions. See the [project and tenancy review](docs/PROJECT_REVIEW_2026-11-01.md) for corrections, verification scope and recommendations, including the current user-wide role model.
