from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ScheduledEmploymentChange
from core.services.employees import apply_scheduled_change


class Command(BaseCommand):
    help = "Apply unapplied scheduled employment changes due today or earlier."

    def handle(self, *args, **options):
        changes = list(ScheduledEmploymentChange.objects.filter(
            applied_at__isnull=True, effective_date__lte=timezone.localdate()
        ).order_by("effective_date", "pk"))
        for change in changes:
            apply_scheduled_change(change)
        self.stdout.write(self.style.SUCCESS(f"Applied {len(changes)} scheduled employment change(s)."))
