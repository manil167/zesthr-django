from django.core.management.base import BaseCommand, CommandError
from core.models import EmployeeDocument
from core.services.documents import process_purge


class Command(BaseCommand):
    help = "Process explicitly requested document deletions whose retention has expired."

    def handle(self, *args, **options):
        failures, count = 0, 0
        ids = EmployeeDocument.objects.filter(purge_requested_at__isnull=False, purged_at__isnull=True).values_list("pk", flat=True)
        for document_id in ids.iterator(chunk_size=200):
            try:
                count += process_purge(document_id)
            except Exception:
                failures += 1
                self.stderr.write(f"Document {document_id}: deletion failed; retry is safe.")
        self.stdout.write(f"Purged {count} document(s).")
        if failures:
            raise CommandError(f"{failures} document deletion(s) need retry.")
