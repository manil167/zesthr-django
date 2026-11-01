"""Copy the data of an old SQLite database into the configured (PostgreSQL) database.

Controlled procedure, in this order:
  1. refuse unless the source has all migrations applied (run `migrate` on a COPY first),
  2. refuse unless the target has all migrations applied and holds no business data,
  3. copy everything inside ONE transaction (all or nothing),
  4. verify counts, ids, links and money totals; any mismatch rolls everything back.

Use --dry-run to do all of the above and then roll back.
"""
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.models import Sum
from django.db.migrations.executor import MigrationExecutor

from core import models as m

LEGACY = "legacy"
BUSINESS_MODELS = [m.Department, m.JobPosition, m.EmploymentType, m.WorkLocation, m.WorkSchedule,
                   m.Employee, m.Attendance, m.Salary, m.LeaveApplication, m.Holiday,
                   # history tables: a copy that silently lost these would be worse than one that lost data
                   m.AttendanceAudit, m.AttendanceCorrectionRequest, m.EmploymentHistory]
# Rows that migration 0007 creates in every fresh database. They are replaced by the
# source's own employment types, so they do not count as "existing data" in the target.
SEEDED_EMPLOYMENT_TYPES = {"Full-time", "Part-time", "Contract", "Intern", "Casual"}
# Not copied: rebuilt automatically (content types, permissions) or meaningless elsewhere.
EXCLUDE = ["contenttypes", "auth.permission", "sessions", "admin.logentry"]


def register_alias(path):
    config = {"ENGINE": "django.db.backends.sqlite3", "NAME": str(path)}
    # configure_settings() fills in Django's defaults but insists on a "default" entry.
    configured = connections.configure_settings({"default": connections.settings["default"], LEGACY: config})
    connections.settings[LEGACY] = configured[LEGACY]


def unregister_alias():
    connections[LEGACY].close()
    connections.settings.pop(LEGACY, None)
    if hasattr(connections._connections, LEGACY):
        delattr(connections._connections, LEGACY)


def pending_migrations(alias):
    executor = MigrationExecutor(connections[alias])
    return executor.migration_plan(executor.loader.graph.leaf_nodes())


def fingerprint(using):
    """Everything that must be identical before and after the copy."""
    User = get_user_model()
    fp = {"counts": {}, "ids": {}}
    for model in BUSINESS_MODELS:
        qs = model.objects.using(using)
        fp["counts"][model.__name__] = qs.count()
        fp["ids"][model.__name__] = sorted(qs.values_list("pk", flat=True))
    fp["counts"]["User"] = User.objects.using(using).count()
    fp["counts"]["UserGroupLinks"] = User.groups.through.objects.using(using).count()
    fp["usernames"] = sorted(User.objects.using(using).values_list("username", flat=True))
    fp["employee_accounts"] = dict(m.Employee.objects.using(using).filter(user__isnull=False)
                                   .values_list("pk", "user__username"))
    fp["manager_links"] = dict(m.Employee.objects.using(using).filter(manager__isnull=False)
                               .values_list("pk", "manager_id"))
    fp["user_groups"] = {u.username: sorted(g.name for g in u.groups.all())
                         for u in User.objects.using(using).prefetch_related("groups")}
    zero = Decimal("0")
    fp["salary_net_total"] = m.Salary.objects.using(using).aggregate(t=Sum("net"))["t"] or zero
    fp["basic_salary_total"] = m.Employee.objects.using(using).aggregate(t=Sum("basic_salary"))["t"] or zero
    fp["leave_status"] = sorted(m.LeaveApplication.objects.using(using).values_list("pk", "status"))
    return fp


class Command(BaseCommand):
    help = "Copy data from a SQLite database file into the default database, with verification."

    def add_arguments(self, parser):
        parser.add_argument("--source-sqlite", required=True, help="Path to the (migrated) SQLite file")
        parser.add_argument("--dry-run", action="store_true", help="Copy and verify, then roll back")

    def handle(self, *args, source_sqlite, dry_run, **options):
        path = Path(source_sqlite)
        if not path.is_file():
            raise CommandError(f"Source database not found: {path}")
        default_name = connections["default"].settings_dict.get("NAME")
        if str(default_name) == str(path):
            raise CommandError("Source and target are the same database.")

        register_alias(path)
        try:
            self._copy(dry_run)
        finally:
            unregister_alias()

    def _copy(self, dry_run):
        if pending_migrations(LEGACY):
            raise CommandError("The source database has unapplied migrations. Make a COPY of it, run "
                               "`python manage.py migrate` against the copy, and use the copy as the source.")
        if pending_migrations("default"):
            raise CommandError("The target database has unapplied migrations. Run `migrate` first.")
        User = get_user_model()
        extra_types = set(m.EmploymentType.objects.using("default").values_list("name", flat=True)) - SEEDED_EMPLOYMENT_TYPES
        if extra_types:
            raise CommandError("The target already contains EmploymentType rows that are not the defaults; "
                               "refusing to mix data. Use an empty, freshly migrated database.")
        for model in (*[b for b in BUSINESS_MODELS if b is not m.EmploymentType], User):
            if model.objects.using("default").exists():
                raise CommandError(f"The target already contains {model.__name__} rows; refusing to "
                                   "mix data. Use an empty, freshly migrated database.")

        before = fingerprint(LEGACY)
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "legacy.json"
            call_command("dumpdata", database=LEGACY, natural_foreign=True, natural_primary=True,
                         exclude=EXCLUDE, output=str(dump), verbosity=0)
            with transaction.atomic(using="default"):
                m.EmploymentType.objects.using("default").filter(name__in=SEEDED_EMPLOYMENT_TYPES).delete()
                call_command("loaddata", str(dump), database="default", verbosity=0)
                self._verify(before, fingerprint("default"))
                if dry_run:
                    transaction.set_rollback(True, using="default")
        total = sum(before["counts"].values())
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"Dry run OK: {total} rows copied and verified, then rolled back. "
                "(PostgreSQL id sequences are not transactional; they may have advanced, which is harmless.)"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Copied and verified {total} rows."))

    def _verify(self, source, target):
        problems = []
        for key in source:
            if key == "user_groups":
                continue
            if source[key] != target[key]:
                if isinstance(source[key], dict) and key == "counts":
                    for name in source[key]:
                        if source[key][name] != target[key][name]:
                            problems.append(f"{name}: source {source[key][name]} != target {target[key][name]}")
                else:
                    problems.append(f"{key} differs between source and target")
        # Groups on the target may include roles the source never had (created by sync_roles);
        # what matters is that every source membership survived.
        for user, groups in source["user_groups"].items():
            if not set(groups) <= set(target["user_groups"].get(user, [])):
                problems.append(f"group memberships of {user} were not preserved")
        if problems:
            raise CommandError("Verification failed, nothing was copied:\n  - " + "\n  - ".join(problems))
        self.stdout.write("Verification passed:")
        for name, n in target["counts"].items():
            self.stdout.write(f"  {name:<16}{n}")
