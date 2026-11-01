# Deploying HROS on Vercel

Vercel runs Django as a serverless function: it detects `manage.py`, reads `WSGI_APPLICATION` from the settings,
runs `collectstatic` for you and serves static files from its CDN. What it does **not** do: run migrations,
keep a database, or run background jobs. Those are covered below.

## What you need
* the code on GitHub, a Vercel account
* a **PostgreSQL** database (SQLite cannot work on Vercel). Easiest: **Neon**, added from Vercel's Marketplace
  (Storage tab > Create > Neon). It gives you two addresses: a *pooled* one (host contains `-pooler`) and an
  *unpooled / direct* one.

## Steps

1. **Create the database.** In Neon, copy the pooled address (`DATABASE_URL`) and the direct address
   (`DATABASE_URL_UNPOOLED`). Both should end in `?sslmode=require`.
2. **Import the project.** Vercel > Add New > Project > pick the repo. Framework is detected as Django.
   Do not set a build or install command. In *Settings > Environment Variables*, tick
   **Automatically expose System Environment Variables** (this is how the app learns its own web address).
3. **Add environment variables** (Production, and Preview if you use previews):

   | Name | Value |
   |---|---|
   | `DJANGO_SETTINGS_MODULE` | `payroll_project.settings.production` |
   | `DJANGO_SECRET_KEY` | a new random key: `python -c "import secrets; print(secrets.token_urlsafe(60))"` |
   | `DATABASE_URL` | the **pooled** Neon address |
   | `DB_POOLED` | `True` |
   | `DJANGO_TRUST_PROXY_SSL_HEADER` | `True` (Vercel terminates HTTPS and sets `X-Forwarded-Proto`) |
   | `DJANGO_TRUST_X_FORWARDED_FOR` | `True` (Vercel overwrites the header, so the audit log gets real addresses) |
   | `DJANGO_TIME_ZONE` | e.g. `Africa/Lagos` |
   | `DJANGO_EMAIL_HOST`, `DJANGO_EMAIL_HOST_USER`, `DJANGO_EMAIL_HOST_PASSWORD`, `DJANGO_EMAIL_PORT`, `DJANGO_DEFAULT_FROM_EMAIL` | your SMTP provider (needed for password reset; the app refuses to start without a host) |
   | `DJANGO_HSTS_SECONDS` | `0` for now (see step 8) |
   | `DJANGO_CSRF_TRUSTED_ORIGINS` | only if you use a custom domain that is not the production domain Vercel reports, e.g. `https://hr.example.com` |

   `DJANGO_ALLOWED_HOSTS` is optional: the app adds Vercel's own addresses automatically (no wildcards).
   The build also reads these variables, so every one above must exist for the environment you deploy.
4. **Deploy.** Push to the main branch (or *Deployments > Redeploy*). The first visit will fail with a database
   error until step 5 is done. That is expected.
5. **Create the tables (the step that is easy to forget).** Run this **once from your own computer**, against the
   **unpooled** address, and again after any release that adds migrations:

   ```powershell
   # PowerShell (VS Code terminal), project folder, virtual environment active
   $env:DJANGO_SETTINGS_MODULE = "payroll_project.settings.production"
   $env:DJANGO_SECRET_KEY      = "any-long-random-text-for-this-command"
   $env:DATABASE_URL           = "<Neon UNPOOLED address>"
   $env:DJANGO_EMAIL_HOST      = "smtp.example.com"
   $env:DJANGO_DEFAULT_FROM_EMAIL = "HROS <hr@example.com>"
   $env:DJANGO_ALLOWED_HOSTS   = "localhost"
   python manage.py migrate
   python manage.py createsuperuser
   ```

   (macOS/Linux: use `export NAME=value`.) `migrate` also creates the six roles and the audit protections.
6. **Check it.** Open `https://<your-app>.vercel.app/healthz/`: you should see `{"status": "ok"}`. If it says
   `unavailable`, the database address or password is wrong. Then open `/login/` and sign in as the superuser.
7. **Optional demo data:** `python manage.py seed_demo` refuses to run unless `DEBUG` is on, so never use it on
   production. Create real employees from the app instead.
8. **Turn HTTPS strictness up.** Once everything works over HTTPS, raise `DJANGO_HSTS_SECONDS` step by step:
   `3600`, then `86400`, then `31536000`. Redeploy after each change.
9. **Custom domain:** Vercel > Settings > Domains. The production domain is picked up automatically.

## Updating
Push to GitHub and Vercel redeploys. If the release contains new migrations, run step 5's `migrate` **before or
right after** it goes live. *Instant Rollback* in Vercel restores the old code but **not** the database, so make
migrations backward compatible or take a Neon backup/branch first.

## Troubleshooting

| You see | Cause and fix |
|---|---|
| `relation "..." does not exist` (for example `django_site`, `core_employee`) | Migrations were never run on **this** database. Do step 5 with the same database the app uses. Check you did not migrate a different one (local SQLite, another Neon branch). |
| `ImproperlyConfigured: Required environment variable ...` in the **build** log | A variable is missing for that environment (Production vs Preview). Add it and redeploy. |
| `400 Bad Request` / `DisallowedHost` | The address is not allowed. Add it to `DJANGO_ALLOWED_HOSTS`, or enable *Automatically expose System Environment Variables*. |
| `403 CSRF verification failed` on login | Your domain is missing from `DJANGO_CSRF_TRUSTED_ORIGINS` (must start with `https://`), or `DJANGO_TRUST_PROXY_SSL_HEADER` is not `True`. |
| Redirect loop | `DJANGO_TRUST_PROXY_SSL_HEADER` is not `True`. |
| `prepared statement ... does not exist` / cursor errors | `DB_POOLED` is not `True` while using a pooled address. |
| Pages unstyled | The build did not run `collectstatic` (check `STATIC_ROOT` is set and the build log). |
| Slow first request | Cold start of a serverless function. Normal after idle time. |

## Limits to know about
* **No background or scheduled jobs.** Expired login sessions accumulate in the database; run
  `python manage.py clearsessions` (same environment as step 5) now and then. Notification and report jobs
  arrive in a later phase and will use Vercel Cron.
* **Requests are limited to 60 seconds** (`vercel.json`), enough for payroll of a few thousand people.
* **Nothing can be saved to disk.** The app stores everything in PostgreSQL, so this is fine today; user file
  uploads would need external storage.
* Each preview deployment runs the production settings. Give Preview its **own** database (a Neon branch) so
  tests never touch real employee data.
