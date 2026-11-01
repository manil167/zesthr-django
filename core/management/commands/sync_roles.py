from django.core.management.base import BaseCommand

from core.roles import ROLE_PERMISSIONS, sync_roles


class Command(BaseCommand):
    help = "Create/refresh the role groups and their permissions (see core/roles.py)."

    def handle(self, *args, **options):
        sync_roles()
        self.stdout.write(self.style.SUCCESS(f"Synced {len(ROLE_PERMISSIONS)} roles."))
