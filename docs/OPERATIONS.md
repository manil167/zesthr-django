# Operations and recovery

## Release checks

Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, the full Django suite, and `python -m pip_audit` in an isolated environment. The GitHub workflow runs the suite on SQLite and PostgreSQL and requires at least 75% coverage. Its lint check detects syntax and undefined-name errors; it does not assert complete style compliance.

Use `docker compose -p hros-verification -f compose.test.yml up -d --wait` for the disposable local PostgreSQL database. Its credentials are test fixtures; never use them in production. Set TEST_DB_* and DJANGO_SETTINGS_MODULE=payroll_project.settings.test_postgres to run the integration suite. Shut down only this Compose project when finished.

## Production startup

Render uses `python scripts/start_server.py`. It checks configuration, applies migrations under a PostgreSQL transaction advisory lock, checks for remaining migrations, and starts Gunicorn only after success. Existing deployments using the old Gunicorn command also load the preparation hook in gunicorn.conf.py. Use a direct database connection for migrations where possible. A competing deployment waits for the lock and fails after the configured timeout rather than starting against an unknown schema.

`/healthz/` returns 503 when the database is unavailable or migrations are pending. Responses disable caching and do not expose database errors. Configure this path as the Render health check and deploy after CI checks pass. The Blueprint describes these settings; existing services must also have their Dashboard settings updated.

The Docker runtime runs as UID 10001. Production secrets are supplied at runtime, never copied into the image. Keep the database, private documents, and backups outside ephemeral application storage.

## Backups

Run `python manage.py backup_database --directory /private/backup/path` in a trusted operator environment with database access. PostgreSQL requires a compatible pg_dump client. The command creates a custom-format dump (or a consistent SQLite backup) and a JSON manifest containing its SHA-256, size, timestamp, and migration names. Passwords are passed through the process environment and are not printed. Restrict the operator environment and directory; Windows deployments also require explicit filesystem ACLs.

Copy backups to encrypted durable storage with a restricted backup identity. A local checksum detects corruption; it is not protection against an attacker able to replace both files. Protect the manifest with the same access controls and consider signed manifests. Schedule backups according to an agreed recovery point objective and retain independently protected copies. This repository does not claim that a production backup schedule is active.

Before restoring, stop writes and preserve a fresh copy of the current database. Verify the checksum, restore into a separate empty database, run Django checks and migration checks against that database, and verify employee links, attendance history, payroll amounts, and audit history. PostgreSQL operators can use `pg_restore --no-owner --no-acl --dbname=<isolated-target> <backup.dump>`. Do not restore over the live database as a verification exercise. Test recovery regularly.

## Rollback

A previous application image may be incompatible with newly migrated data. Review each migration before release; prefer additive schema changes and later cleanup. Roll back application code only when its schema compatibility is established. Otherwise deploy a forward fix or follow the verified restore procedure with an explicitly agreed recovery point. Never delete payroll or audit history to repair a deployment.

## Employee documents

Production uploads remain disabled until private durable storage has been provisioned and verified. Use the S3/KMS configuration in infra/private-documents.tf, configure the documented environment variables privately, and run `python manage.py check_private_document_storage` before enabling uploads. Public access must remain blocked. Downloads pass through application authorization and are audited.

S3 versioning preserves prior object versions. An application purge does not establish that prior versions or backups have expired. Define an authorized retention policy for versions and backups before promising permanent deletion. Infrastructure provisioning, secret configuration, recovery objectives, and retention approval remain operational tasks requiring the deployment owner's account details.


### Reusing an existing KMS key

Set Terraform `region`, `bucket_name`, `expected_account_id`, and `existing_kms_key_arn` in an ignored private tfvars file or your infrastructure secret/configuration store. Use the full key ARN. The configuration restricts the provider to the expected account and rejects a key from a different region/account. It references an existing key without taking over its lifecycle or changing its key policy. The key must be enabled, symmetric, and usable for encryption/decryption. Its key policy must permit the application identity directly or enable the applicable IAM authorization. An IAM policy alone may be insufficient; verify both layers.

Without existing_kms_key_arn the configuration retains its dedicated-key behavior. A moved block preserves the address of a previously managed key when upgrading the configuration. Do not switch an already provisioned managed-key deployment to an external key without reviewing state and the plan: prevent_destroy is intentional protection for historical documents. If the bucket already exists, import and review its current configuration before applying this resource definition; do not attempt to recreate it or overwrite policies blindly.

Run Terraform initialization, validation, and a reviewed plan with securely authenticated credentials for the intended account before applying. HCL parsing alone is not provider validation. Never put AWS access keys, session tokens, or Terraform state in Git or chat. Supply only the application identity's restricted credentials to Render. Set DJANGO_DOCUMENT_REGION, DJANGO_DOCUMENT_BUCKET, and DJANGO_DOCUMENT_KMS_KEY_ID from the verified outputs, run the storage verification command, and only then enable uploads.
