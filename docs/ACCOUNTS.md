# Accounts and sign-in

## Account lifecycle

| Stage | What happens |
|---|---|
| **Provisioned** | An administrator creates the user in `/admin/` (Users → Add). The account is flagged *must change password*. Link it to an employee under Edit employee → Login account (HR Administrator). |
| **First sign-in** | Every page redirects to *Change password* until the user chooses their own. Only that page and *Sign out* are reachable meanwhile. |
| **Normal use** | Sessions expire after `DJANGO_SESSION_IDLE_MINUTES` (default 60) of inactivity; each request renews the timer. |
| **Forgot password** | *Forgot your password?* emails a single-use link valid for `DJANGO_PASSWORD_RESET_MINUTES` (default 60). The answer is identical whether or not the address exists. Needs the **user's** email (set in `/admin/`), not the employee record's email. |
| **Password change** | Signs out every other device. Passwords need 10+ characters and may not be common, numeric-only or similar to the username. |
| **Deactivation** | `User.is_active = False`. Sign-in is refused and an open session stops working on its next request. Deactivating an *employee* (Deactivate action) deactivates their login too; so does the *Deactivate selected accounts* action in `/admin/` (you cannot deactivate yourself). Reactivation restores access. History is never deleted. |

## Sign-in protection

* 5 failed attempts for the same username from the same address within 15 minutes block further attempts
  (HTTP 429, the same message for unknown names). A successful sign-in resets the count; the block lifts
  by itself. Tune with `DJANGO_LOGIN_MAX_FAILURES` and `DJANGO_LOGIN_FAILURE_WINDOW_MINUTES`.
* Password-reset emails are limited to 5 requests for the same submitted address from the same address
  within 15 minutes. Excess requests receive the standard success response and send no email, preventing
  account enumeration. Tune with `DJANGO_PASSWORD_RESET_MAX_REQUESTS` and
  `DJANGO_PASSWORD_RESET_WINDOW_MINUTES`.
* The login page shows demo credentials **only** when `DEBUG` is on.

## Audit log

Every sign-in, failed sign-in, block, sign-out, password change/reset, forced-change flag and
activation change is recorded in *Security events* (`/admin/core/securityevent/`) with time, account,
address and browser. Passwords are never stored. Entries cannot be edited or deleted from the application.
Readable by **System Administrators** and superusers only (they need *Staff status* to open `/admin/`).

Address note: behind a reverse proxy every request appears to come from the proxy unless you set
`DJANGO_TRUST_X_FORWARDED_FOR=True`. Enable it **only** if your proxy overwrites that header, otherwise
clients can forge their address in the log and sidestep the sign-in block.

## Roles

HROS provisions the roadmap roles as Django groups. Each group receives only
the permissions supported by the currently shipped modules; empty Recruiter and
Team Lead groups are intentional placeholders until recruitment and team
workflows exist. `is_superuser` remains the break-glass platform-administration
capability and should be tightly controlled.

## Email (password reset)

Development prints emails to the terminal. Production requires `DJANGO_EMAIL_HOST` and
`DJANGO_DEFAULT_FROM_EMAIL` (plus `DJANGO_EMAIL_HOST_USER` / `DJANGO_EMAIL_HOST_PASSWORD`,
`DJANGO_EMAIL_PORT`, `DJANGO_EMAIL_USE_TLS`) and refuses to start without them.

## Known limits

* No two-factor authentication yet.
* No automatic purge of old security events (a retention job comes with the operations phase).
