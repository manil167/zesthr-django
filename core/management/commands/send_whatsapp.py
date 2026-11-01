from django.core.management.base import BaseCommand, CommandError
from core.services.whatsapp import deliver


class Command(BaseCommand):
    help = 'Deliver queued WhatsApp responses. Schedule every 10–30 seconds; credentials remain in environment settings.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 500:
            raise CommandError('--limit must be between 1 and 500')
        try:
            sent = deliver(options['limit'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Sent {sent} WhatsApp response(s).')
