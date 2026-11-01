"""Create-only administrator provisioning from private runtime configuration."""
import os
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.views.decorators.debug import sensitive_variables


class Command(BaseCommand):
    help = 'Create an initial admin only when DJANGO_BOOTSTRAP_ADMIN=True.'

    @sensitive_variables('password', 'user')
    @transaction.atomic
    def handle(self, *args, **options):
        if os.environ.get('DJANGO_BOOTSTRAP_ADMIN', '').strip().lower() not in {'1', 'true', 'yes', 'on'}:
            return
        if getattr(settings, 'NEON_AUTH_BASE_URL', ''):
            if os.environ.get('NEON_ADMIN_USER_ID', '').strip() and os.environ.get('NEON_ADMIN_EMAIL', '').strip():
                self.stdout.write('Neon administrator setup enabled: the configured verified Neon identity receives administrator access at its next sign-in. No local password is created or changed.')
            else:
                self.stdout.write('Neon Auth is enabled; local-password administrator bootstrap skipped. Set NEON_ADMIN_USER_ID and NEON_ADMIN_EMAIL to authorize an exact verified Neon user.')
            return
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', '').strip()
        if not username:
            raise CommandError('Set DJANGO_SUPERUSER_USERNAME for administrator bootstrap.')
        User = get_user_model()
        existing = User.objects.filter(username=username).first()
        if existing:
            if not (existing.is_superuser and existing.is_staff and existing.is_active):
                raise CommandError('The configured username already exists and is not an active platform administrator. No account was changed.')
            self.stdout.write('Administrator already exists; credentials and permissions unchanged.')
            return
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')
        if not email or not password:
            raise CommandError('Set DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD securely in the runtime environment.')
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError('The configured email already belongs to an account. No account was changed.')
        user = User(username=username, email=email, is_active=True, is_staff=True, is_superuser=True)
        user.set_password(password)
        try:
            user.full_clean()
            validate_password(password, user=user)
        except ValidationError:
            raise CommandError('Administrator details are invalid or the password is too weak. Use a valid unique username/email and a strong password of at least 10 characters.') from None
        user.save()
        self.stdout.write(self.style.SUCCESS('Initial administrator created. Disable DJANGO_BOOTSTRAP_ADMIN and remove DJANGO_SUPERUSER_PASSWORD after successful login.'))
