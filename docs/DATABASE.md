# Database guide

HROS uses **PostgreSQL in production** and **SQLite for local development**. The same
migrations run on both, and the whole test suite is run against both.

## Which settings use which database

| Settings module | Database |
|---|---|
| `payroll_project.settings.development` | SQLite file (`db.sqlite3`, or `DJANGO_SQLITE_PATH`) |
| `payroll_project.settings.test` | SQLite in memory |
| `payroll_project.settings.test_postgres` | PostgreSQL (integration tests) |
| `payroll_project.settings.production` | PostgreSQL (required) |

## Connecting production to PostgreSQL

Set **either** one URL (what Neon, Supabase, Render and Railway give you):

    DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require

**or** the separate values `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

| Variable | Default | Meaning |
|---|---|---|
| `DB_SSLMODE` | `require` | Encrypted connection. Use `disable` only for an isolated disposable test database. Overrides `?sslmode=` in the URL. |
| `DB_CONN_MAX_AGE` | `60` | Seconds a connection is reused. |
| `DB_CONNECT_TIMEOUT` | `10` | Seconds before giving up connecting. |
| `DB_DISABLE_SERVER_SIDE_CURSORS` | `False` | Set `True` behind a transaction pooler (pgbouncer, Neon's pooled endpoint). |

The password may contain special characters; URL-encode them in `DATABASE_URL` (`@` becomes `%40`).

## Running the tests against PostgreSQL

    docker compose -p hros-verification -f compose.test.yml up -d --wait

Use TEST_DB_NAME=hros, TEST_DB_USER=hros, TEST_DB_PASSWORD=hros-integration-only,
TEST_DB_HOST=127.0.0.1, TEST_DB_PORT=55432 and
DJANGO_SETTINGS_MODULE=payroll_project.settings.test_postgres. On PowerShell set
each variable with `$env:NAME='value'`, then run `python manage.py test`.

Django creates and drops test_hros. The disposable test role needs CREATEDB;
never grant production application credentials this privilege merely to run tests.
CI verifies PostgreSQL 17. The Compose fixture binds only to localhost.

## Moving existing SQLite data to PostgreSQL

Do this once, with the site stopped, and **keep the original `db.sqlite3` untouched**.

1. Work on a copy and bring it to the latest schema:

       cp db.sqlite3 legacy.sqlite3
       DJANGO_SETTINGS_MODULE=payroll_project.settings.development \
       DJANGO_SQLITE_PATH=legacy.sqlite3 python manage.py migrate

2. Point the production environment at an **empty** PostgreSQL database and create the schema:

       python manage.py migrate

3. Rehearse. This copies, verifies, then rolls everything back:

       python manage.py migrate_legacy_data --source-sqlite legacy.sqlite3 --dry-run

4. Copy for real (all-or-nothing, in one transaction):

       python manage.py migrate_legacy_data --source-sqlite legacy.sqlite3

The command refuses to run if the source has unapplied migrations, if the target already
holds data, or if source and target are the same file. It verifies row counts, primary
keys, which login belongs to which employee, manager links, group memberships and money
totals; any difference cancels the whole copy. Passwords keep working (hashes are copied).
Sessions, content types and permissions are not copied; they are rebuilt, so users sign in
again once.

After the copy, check the site, then archive `legacy.sqlite3` and `db.sqlite3`.
**They contain real employee data: keep them out of Git and delete when no longer needed.**

## Backup and restore

Use `python manage.py backup_database --directory <private-path>` from a trusted
operator environment with a compatible PostgreSQL client. It writes a custom-format
dump and checksum manifest without printing database credentials. Production backups
must use encrypted durable storage with restricted access. See [operations](OPERATIONS.md)
for restore rehearsal, verification, retention, and rollback procedures.

CI restores a PostgreSQL fixture into an isolated empty database and verifies employee
links, exact payroll amounts, and attendance audit attribution. This demonstrates the
recovery procedure; it does not establish a production backup schedule or recovery objective.

## What the database enforces by itself

Rules the database refuses to break even if application code is bypassed:

* one attendance row per employee per day; one salary row per employee per month
* unique employee numbers, organization-scoped department names/codes, and schedule names
* salary, deductions and net pay cannot be negative
* a leave cannot end before it starts
* an employee cannot be their own manager
* the `is_active` flag always agrees with the employment status
* resigned/terminated/retired employees must have a separation date that is not before joining
* employees with attendance, leave or payroll history cannot be deleted (deactivate instead)
