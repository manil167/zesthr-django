"""Live service checks; uses disposable writes and never displays credentials."""
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from apps.organizations.models import Organization
from core.services import neon_auth


class Command(BaseCommand):
    help = 'Test configured Neon PostgreSQL and private object storage. Auth signup/current-user is tested by npm run test:neon-auth.'

    def handle(self, *args, **options):
        failures = []
        if connection.vendor != 'postgresql':
            failures.append('PostgreSQL: DATABASE_URL is not configured (using SQLite).')
        else:
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT 1')
                        assert cursor.fetchone()[0] == 1
                    suffix = uuid.uuid4().hex[:16]
                    row = Organization.objects.create(name='Neon service verification ' + suffix,
                                                      code='VERIFY_' + suffix.upper())
                    assert Organization.objects.get(pk=row.pk).name.endswith(suffix)
                    transaction.set_rollback(True)
                self.stdout.write('PASS PostgreSQL: connected, wrote/read organization, rolled back disposable data.')
            except Exception as exc:
                failures.append('PostgreSQL: ' + type(exc).__name__ + ' (details omitted to protect connection credentials).')
        options = settings.STORAGES.get('private_documents', {}).get('OPTIONS', {})
        if not options.get('endpoint_url'):
            failures.append('Object storage: AWS_ENDPOINT_URL_S3 is not configured for Neon.')
        else:
            storage = storages['private_documents']
            name, saved = 'verification/' + uuid.uuid4().hex + '.txt', None
            payload = b'Private Neon object storage verification\n'
            try:
                saved = storage.save(name, ContentFile(payload))
                with storage.open(saved, 'rb') as file:
                    assert file.read() == payload
                storage.delete(saved)
                assert not storage.exists(saved)
                self.stdout.write('PASS Object storage: private upload, exact download, delete verified.')
            except Exception as exc:
                failures.append('Object storage: ' + type(exc).__name__ + ' (credentials and response omitted).')
                if saved:
                    try:
                        storage.delete(saved)
                    except Exception:
                        self.stderr.write('A disposable verification object may require administrator cleanup.')
        if not neon_auth.enabled():
            failures.append('Auth: NEON_AUTH_BASE_URL is not configured. Signup/current-user test remains unrun.')
        else:
            self.stdout.write('Auth configured; run npm run test:neon-auth with secure test-user environment variables.')
        if failures:
            raise CommandError('\n'.join(failures))
