from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.services.anomalies import scan


class Command(BaseCommand):
    help = "Identify reviewable attendance exceptions from the last 30 days without changing attendance."

    def handle(self, *args, **options):
        today = timezone.localdate()
        count = scan(today - timedelta(days=30), today)
        self.stdout.write(self.style.SUCCESS(f"Identified {count} new attendance exception(s)."))
