"""Consistent database backups with checksums; never prints database credentials."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import uuid
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone


class Command(BaseCommand):
    help = "Write a consistent database backup and checksum manifest into a private directory."

    def add_arguments(self, parser):
        parser.add_argument("--directory", default=str(settings.BASE_DIR / "backups"))

    def handle(self, *args, directory, **options):
        root = Path(directory).resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        vendor = connection.vendor
        if vendor not in ("sqlite", "postgresql"):
            raise CommandError("Only SQLite and PostgreSQL backups are supported.")
        executable = shutil.which("pg_dump") if vendor == "postgresql" else None
        if vendor == "postgresql" and executable is None:
            raise CommandError("Install a compatible PostgreSQL pg_dump client in the backup worker.")
        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        extension = ".sqlite3" if vendor == "sqlite" else ".dump"
        target = root / f"hros-{stamp}-{uuid.uuid4().hex[:8]}{extension}"
        descriptor = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            if vendor == "sqlite":
                connection.ensure_connection()
                with closing(sqlite3.connect(target)) as destination:
                    connection.connection.backup(destination)
                    if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise CommandError("SQLite backup integrity verification failed.")
            else:
                config = connection.settings_dict
                environment = dict(os.environ, PGPASSWORD=config.get("PASSWORD", ""),
                    PGSSLMODE=config.get("OPTIONS", {}).get("sslmode", "require"), PGCONNECT_TIMEOUT="15")
                result = subprocess.run([executable, "--format=custom", "--no-owner", "--no-acl",
                    "--host", config["HOST"], "--port", str(config.get("PORT") or 5432),
                    "--username", config["USER"], "--dbname", config["NAME"], "--file", str(target)],
                    env=environment, capture_output=True, text=True, timeout=1800)
                if result.returncode:
                    raise CommandError("Database backup failed. Check the operator's database-client configuration.")
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            manifest = {"created_at": timezone.now().isoformat(), "vendor": vendor, "file": target.name,
                "bytes": target.stat().st_size, "sha256": digest.hexdigest(),
                "migrations": sorted(f"{app}/{name}" for app, name in MigrationRecorder(connection).applied_migrations())}
            manifest_path = target.with_suffix(target.suffix + ".json")
            fd = os.open(str(manifest_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(manifest, output, indent=2)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self.stdout.write(self.style.SUCCESS(f"Backup and checksum manifest created: {target.name}"))
