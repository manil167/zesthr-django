# Neon authentication, tenant isolation and messaging

## Neon PostgreSQL and managed Neon Auth

This project uses Django as its server and permission system. Neon managed Better
Auth authenticates email/password accounts; Django owns explicit organization
memberships, employee profiles and business permissions. Neither a verified email
nor a successful public signup grants tenant access.

1. In your Neon project, enable **Neon Auth / managed Better Auth** on the same
   database branch as the application. Enable required email verification and
   configure a production email provider in Neon.
2. Securely configure `DATABASE_URL` with the branch's PostgreSQL connection URL.
   Prefer the pooled endpoint for web traffic. Set `sslmode=verify-full` (and the
   hosting platform's trusted CA bundle if needed). Neon pooler hostnames are
   detected automatically; `DB_POOLED`, `DB_CONN_MAX_AGE`, and
   `DB_DISABLE_SERVER_SIDE_CURSORS` retain explicit override support. A direct
   endpoint is useful for migrations and backup tools.
3. Set `NEON_AUTH_BASE_URL` to the Auth URL shown in Neon, for example
   `https://<endpoint>.neonauth.<region>.aws.neon.tech/neondb/auth`. Include the
   `/auth` suffix once. Set `NEON_AUTH_APP_ORIGIN` to your exact public website
   origin, such as `https://hr.example.com`. Register that origin and the app's
   login/password-reset callback URLs as trusted destinations in Neon.
4. Configure the existing production requirements (`DJANGO_SECRET_KEY`, explicit
   allowed hosts and CSRF origins, email and private storage settings), then run
   `python manage.py migrate --noinput` and `python manage.py check --deploy`.
   Keep Django's database session backend: provider cookies are retained
   server-side, never in browser-readable session storage. Do not switch to the
   signed-cookie session backend.
5. Sign up, verify the email, then sign in. Neon sessions are revalidated on every
   authenticated request and fail closed on provider errors. Login uses the
   verified provider subject and issuer, never an email match with an existing
   Django account. New Neon identities have unusable local passwords and no roles.

When `NEON_AUTH_BASE_URL` is empty, existing Django login/signup works for local
development. When set, Neon is the only authentication backend; local passwords
and Google/allauth shortcuts do not authenticate. Existing local users require an
explicit, administrator-reviewed identity migration; matching an email alone is
not a safe migration. For a fresh testing administrator, the README documents
an opt-in grant using NEON_ADMIN_USER_ID plus NEON_ADMIN_EMAIL after verified
Neon sign-in; this never merges or takes over the old local account. Preserve a controlled administrative recovery procedure
before enabling Neon on an existing production deployment. The integration does
not create a Neon project or move an existing production database automatically.

Neon provider password reset and password change are integrated. Configure email
in Neon; Django SMTP alone does not configure provider verification/reset email. The app provides code-entry verification for the shared provider; production should configure custom SMTP.

Official protocol references:
- https://neon.com/docs/auth/authentication-flow
- https://neon.com/docs/auth/quick-start/nextjs-api-only

## Organization onboarding and authorization

A platform superuser creates organizations and assigns initial HR accounts their
memberships and roles. Platform superusers deliberately have cross-organization
administrative access. Ordinary staff roles never bypass tenant membership.
Membership management and global role editing are restricted to platform admins.

HR with account-link permissions can use **Notifications → Invite member**.
Invitations are email-bound, expire after 48 hours, and can be accepted once. Share
the invitation privately. With Neon, acceptance occurs only after email verification
and sign-in. Invite acceptance supplies membership only; HR must separately link
the employee record and assign any authorized role. Public signup without an
invitation leaves the account pending and unable to see tenant records. To revoke
access, disable the membership and/or account; do not delete attributable history.

Companies belong to organizations. Branches, divisions, grades and cost centers
belong to companies; teams belong to divisions. Core departments, positions,
employment types, schedules, work locations, holidays and calendars are now
organization-owned. Employee relationships must resolve to that same organization;
company-specific assignments must also match the selected company. Reference
labels can repeat across organizations. Default schedules are resolved per tenant.
Legacy global references are cloned into each existing tenant during migration,
with employee and department references reassigned to preserve prior policies.
A legacy position attached to another tenant's department blocks migration until
its assignment is corrected; migration does not silently move it.

Lists, details, exports, APIs, profile access, form options, admin filters and account
choices use membership scope. Unknown model ownership fails closed in admin/form
querysets. Memberships are cached for one HTTP request only. Revocation takes effect
on the next request and is rechecked for bot commands and queued delivery.

Database triggers enforce tenant relationship consistency and prevent moving an
existing tenant-owned row into another tenant, including SQL/bulk updates that
bypass ModelForms. These guards cover core employee records and organization
reference relationships on SQLite and PostgreSQL. They are **not PostgreSQL RLS**:
the Django database credential has administrative data access and must remain
server-side. Do not expose it through a browser/Data API. A future direct Data API
requires its own RLS policies and restricted database role.

For a future SQLite migration that rebuilds guarded tables, follow migration 0034:
uninstall the tenant relationship guards before the rebuild, then reinstall them
afterward. Re-run migrations from a fresh database on both supported databases.

## Announcements and the website assistant

HR administrators, HR managers and HR officers receive `core.send_announcement`.
**Notifications → Send announcement** accepts one of the sender's active
organizations. The transaction creates one announcement and one notification for
every active member, with batched inserts and stable deduplication keys. No member
of another organization receives it. Revoked members cannot retrieve old
organization notifications. Private notifications are still recipient-owned.

All signed-in users can open **Ask the app guide**. It answers common questions
about attendance, corrections, leave, payslips, access, announcements and WhatsApp.
This is a deterministic help assistant, not a generative AI integration. It sends
no employee records or questions to an AI provider, and does not execute actions.
Organization-specific policy questions go to HR.

## WhatsApp Business Cloud API

Configure your Meta app, WhatsApp Business account, phone number and HTTPS webhook:

- Webhook: `/integrations/whatsapp/webhook/`
- Subscribe to the `messages` webhook field.
- `WHATSAPP_PHONE_NUMBER_ID`: the Meta phone-number ID, not the displayed number.
- `WHATSAPP_DISPLAY_NUMBER`: the employee-facing bot number.
- `WHATSAPP_ACCESS_TOKEN`: securely stored Cloud API access token.
- `WHATSAPP_APP_SECRET`: the Meta app secret, available to the Django process for
  HMAC verification; it cannot be a proxy-only placeholder.
- `WHATSAPP_VERIFY_TOKEN`: your privately configured webhook verification value,
  available to the Django process. Never commit real values.
- `WHATSAPP_API_VERSION`: supported Graph API version (default `v23.0`).

Employees (including HR who punch attendance) need an active employee profile,
active account and active organization membership. On **WhatsApp connection**,
generate a 10-minute code and send `LINK <code>` from the employee's own WhatsApp.
Codes are stored as hashes, single-use and replaced whenever another is generated.
A caller cannot impersonate an employee by supplying an employee ID or a phone
number from a profile. Do not share the connection code. `UNLINK` or Disconnect
revokes the link; queued private replies are cancelled after unlinking.

Commands:

- `IN` / `OUT`: use the same transactional attendance service and audit trail as
  website punches, including overnight and already-punched rules.
- `STATUS`: the linked employee's current shift only.
- `NOTIFICATIONS`: the five latest notifications in that employee's organization.
- `HELP` / `HELP <question>`: command list or app guidance.
- `UNLINK`: revoke the connection.

HR announcements are delivered to every organization's active member **in the
website inbox**. This implementation does not send unsolicited WhatsApp broadcasts.
Proactive WhatsApp broadcasts need opt-in records, approved Meta templates and
separate delivery tracking outside the customer-service window.

The signed webhook rejects forged signatures, ignores other business phone IDs,
and commits a deduplication receipt, attendance action and reply outbox atomically.
Run the worker every 10–30 seconds using your process supervisor or scheduler:

```sh
cd /path/to/zesthr-django
.venv/bin/python manage.py send_whatsapp --limit 50
```

The worker uses PostgreSQL row locks to coordinate concurrent workers, bounded
exponential retries, per-request timeouts and an indexed due queue. It cancels
private replies after account/membership/link revocation and stops stale replies
before the customer-service window expires. Delivery is at least once: a crash
after Meta accepts a request but before local commit can duplicate a reply; inbound
message deduplication still prevents duplicate punches. Monitor `WhatsAppOutbox`
rows with eight attempts and `last_error` for failed delivery. Do not log access
tokens, app secrets, raw webhook bodies or connection codes. Apply your retention
policy to receipts, delivery text, invitations and audit data.

## Cloud networking and verification

Allow HTTPS egress to the exact Neon Auth hostname and `graph.facebook.com`.
PostgreSQL also requires the Neon database hostname and port 5432 through the
cloud environment's supported TCP route; an HTTPS allowlist alone does not grant
raw database connectivity. Store `DATABASE_URL`, HMAC secrets and verification
values as secure runtime variables. A proxy secret scoped to `graph.facebook.com`
can supply the outbound WhatsApp token when supported. Never paste credentials
into chat or commit them to the repository.

Run the full suite on SQLite and the isolated PostgreSQL test service:

```sh
DJANGO_LOAD_DOTENV=False DJANGO_SETTINGS_MODULE=payroll_project.settings.test python manage.py test
python manage.py makemigrations --check --dry-run
docker compose -p hros-tests -f compose.test.yml up -d --wait
# Use the disposable password documented in compose.test.yml, never production credentials.
TEST_DB_PASSWORD=hros-integration-only TEST_DB_PORT=55432 \
  DJANGO_LOAD_DOTENV=False DJANGO_SETTINGS_MODULE=payroll_project.settings.test_postgres \
  python manage.py test
```

Provider HTTP calls are mocked in the automated tests. Production readiness also
requires a real verified Neon signup/login/password-reset cycle and a real signed
Meta webhook, account-link, punch and reply-delivery test using configured accounts.

## Provision this linked project

The repository's `neon.ts` declares managed Auth and a private `assets` bucket.
The local, ignored `.neon` link targets organization `org-steep-field-51623490`,
project `divine-sky-09057039`, branch `production`. Do not create another project,
Git branch or database branch for this task.

```sh
npm install -g neon@latest
neon init -y --org-id org-steep-field-51623490 \
  --project-id divine-sky-09057039 --branch production \
  --services postgres,auth,object-storage
neon deploy
npm ci
npm run build
```

`neon deploy` provisions backend services; Render continues to host Django. It does
not publish Django application changes to Render. Keep the production website
origin `https://zesthr-django.onrender.com` in `NEON_AUTH_APP_ORIGIN` and Neon's trusted
domains. Provider credentials from `.env.local` are loaded automatically by Django;
set those same values securely in Render's environment settings. Never commit the
Neon link, `.env` files, test passwords or storage credentials.

The browser signup form uses `@neon/auth`, bundled locally with esbuild. It calls
the CSRF-protected same-origin Django signup endpoint; Django proxies credentials
to the configured Neon endpoint. Email verification has an app code-entry/resend
page, usable with Neon's shared SMTP, as well as support for verification links
when custom SMTP is configured. Provider cookies remain in server-side Django
sessions after verified login. Logout revokes the provider session and clears the
local session. Browser JavaScript never receives provider session tokens.

`DATABASE_URL_UNPOOLED`, when provided by the CLI, is selected automatically for
`manage.py migrate`, `prepare_deployment`, and production startup migrations. The
production server returns to the pooled URL after preparation. The existing
`psycopg` driver and Django ORM remain appropriate for this Python server runtime.

With `AWS_ENDPOINT_URL_S3` present, private document storage automatically uses the
Neon S3 backend and `assets` bucket, AWS credential chain, path-style addressing
and SigV4. Explicit `DJANGO_DOCUMENT_STORAGE` still overrides that selection.
Set `DJANGO_DOCUMENT_UPLOADS_ENABLED=True` after a live storage check succeeds.
Files remain available only through authorized Django downloads; the bucket must
stay private. Neon does not use the AWS KMS options required by the separate AWS
S3 backend.

Live checks (the commands fail explicitly when prerequisites are missing):

```sh
python manage.py verify_neon_services
# Set NEON_TEST_EMAIL and NEON_TEST_PASSWORD privately, for an account you control.
npm run test:neon-auth
# After email verification, rerun for the existing test account:
NEON_TEST_EXISTING_USER=1 npm run test:neon-auth
npm start
```

The Python service check writes/reads a disposable organization within a rolled-back
PostgreSQL transaction, and uploads/downloads/deletes a private verification object.
The SDK check creates a real test user, signs in, reads back the matching current
user, signs out and verifies the session is absent. Required email verification
can pause that check; complete it with the account you control before rerunning.
Test identities are never automatically promoted or added to an organization.

## Sign-in deployment recovery (31 October 2026)

The production Auth branch initially had no trusted domains. It rejected sign-in with INVALID_ORIGIN and password recovery with INVALID_REDIRECT_URL. The exact website origin https://zesthr-django.onrender.com was added to the existing production branch's trusted domains; no wildcard or verification bypass was introduced.

The existing Render service also retained its old Python-only build command despite the newer Blueprint. Signup then failed with HTTP 500 because core/neon-signup.bundle.js was absent. The Dashboard build command was corrected to:

```sh
pip install -r requirements.txt && npm ci --include=dev && npm run build:auth && python manage.py collectstatic --noinput
```

Build the browser bundle before collecting static assets. Collection must retain the deployment's production settings so the hashed manifest and files agree; isolated test settings belong to build checks, not production collection. Existing Render services do not automatically inherit edited Blueprint settings unless managed and synchronized as a Blueprint.

Live smoke checks confirmed HTTP 200 for login, signup, the hashed signup JavaScript, health, and an unauthenticated provider session lookup. These checks do not establish successful user authentication. Subsequent sign-in requests reached Neon and returned EMAIL_NOT_VERIFIED. The account owner must complete the existing /verify-email/ flow and then sign in; do not bypass provider verification or reset roles to troubleshoot this condition.
