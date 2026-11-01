"""Serialize migrations across overlapping deploys, including pooled PostgreSQL."""
from contextlib import contextmanager
from copy import deepcopy
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections, transaction


@contextmanager
def deployment_lock():
    if connections["default"].vendor != "postgresql":
        yield
        return
    alias = "hros_deployment_lock"
    connections.databases[alias] = deepcopy(connections.databases["default"])
    try:
        with transaction.atomic(using=alias):
            with connections[alias].cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '120s'")
                cursor.execute("SET LOCAL statement_timeout = '180s'")
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [182876842])
            yield
    finally:
        connections[alias].close()
        del connections.databases[alias]
        if hasattr(connections._connections, alias):
            delattr(connections._connections, alias)


class Command(BaseCommand):
    help = "Check configuration and apply migrations under a deployment lock before serving traffic."

    def handle(self, *args, **options):
        call_command("check", fail_level="ERROR")
        with deployment_lock():
            call_command("migrate", interactive=False)
            call_command("migrate", check_unapplied=True)
            call_command("bootstrap_admin")
        self.stdout.write(self.style.SUCCESS("Deployment schema is ready."))
