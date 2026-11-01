# Master-plan implementation record

Last reviewed: 28 October 2026. This is an evidence record, not a declaration that production readiness is complete.

## Delivered on main

- Baseline and security: production settings validation, explicit host trust, tenant-scoped web/admin/API access, restricted account administration, and removal of homepage/footer architecture badges.
- Employment: effective-dated employment changes, validation, tenant-safe administration, and scheduled application with retained history (32fb277).
- Leave: transactional decisions and cancellation history (87c99e8), entitlements, balances, accrual, and immutable adjustments (18b23d2).
- Payroll: reviewed immutable calculation snapshots, recurring components, approved adjustments, independent approval, finalization locks, and reversal history (d559a11).
- Documents: validated private PDF upload, authorized streaming, download audit, retention and retry-safe purge (f8c21a8). Private S3/KMS integration and infrastructure definition added (6cc6c1c); production provisioning remains pending.
- Attendance: reviewable anomalies and immutable review decisions (70dda5f).
- Notifications and reports: private notification inbox and manager leave queues (11921ae), scoped compliance reports with confidential-note controls (1b57dd2).
- Privileged accounts: operator escalation and impersonation restrictions (347939e).

## Verified operations and release checks

- f01b8c4: GitHub CI on SQLite/PostgreSQL, dependency audit, coverage gate, production container, migration preparation/locking, schema-aware readiness, database backups with checksummed manifests, and recovery documentation.
- Local SQLite: 519 tests passed; five PostgreSQL-only concurrency tests skipped. Targeted regressions: 19 passed. Dependency audit: 59 packages, zero advisories. Lint and migration drift passed.
- GitHub run 40640173931: SQLite, PostgreSQL (including concurrency tests), dependencies, and container jobs passed. Container verification confirms non-root UID and migration startup. Local Docker failed; successful CI provides independent container/PostgreSQL evidence.
- 5b761c4 / GitHub run 40640732203: all four jobs passed, including an isolated PostgreSQL dump/restore exercise verifying employee/account/manager links, exact salary amounts, and attendance-audit attribution.
- Render deployed f01b8c4 successfully; public /healthz/ returned HTTP 200 with status ok and Cache-Control no-store. Existing Gunicorn startup uses the verified compatibility migration hook.
- f86d7f0: remaining homepage technical copy removed; sample labels clarified; keyboard skip link, focus indicators, menu state, and Escape handling added. Fourteen existing dashboard/access tests passed. Follow-up zero-count/empty-feed accuracy fix passed four homepage/access tests.

## Remaining plan review

- Finish Render Dashboard settings: start command python scripts/start_server.py, health check /healthz/, and automatic deployment after CI checks pass. The live service still has its original command, blank health-check setting, and commit-triggered deployment. Desktop automation could not reliably capture the browser window; no Dashboard changes were submitted.
- Complete visual mobile/desktop and keyboard review when reliable browser access is available. Account provisioning and employee linking provide the current onboarding path; a task-based onboarding wizard is not implemented. Per-location holiday integration remains conditional on confirmed multi-branch requirements.
- Final production review still needs operator configuration validation, approved company policies, and realistic production-volume performance measurements. Existing query-budget tests and CI coverage do not establish production load capacity.
- Provision and verify private durable document storage before enabling production uploads. Requires the owner's cloud account/region and secure credential setup.
- Confirm and implement the production backup schedule, retention policy, and recovery objectives. Commands alone do not establish an operating backup service.

Native mobile development and broader multi-branch expansion remain conditional features in the request. A versioned API and existing work-location model provide foundations; they do not establish that a mobile application or every multi-branch business policy is complete.
