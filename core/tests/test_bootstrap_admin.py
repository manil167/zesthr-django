import os
from io import StringIO
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings


@override_settings(NEON_AUTH_BASE_URL='')
class BootstrapAdminTests(TestCase):
    values = {'DJANGO_BOOTSTRAP_ADMIN': 'True', 'DJANGO_SUPERUSER_USERNAME': 'platform-owner',
              'DJANGO_SUPERUSER_EMAIL': 'owner@example.com',
              'DJANGO_SUPERUSER_PASSWORD': 'Distinct!AdminPassword2937'}

    def bootstrap(self, **overrides):
        output = StringIO()
        with patch.dict(os.environ, {**self.values, **overrides}):
            call_command('bootstrap_admin', stdout=output)
        self.assertNotIn(self.values['DJANGO_SUPERUSER_PASSWORD'], output.getvalue())

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            call_command('bootstrap_admin', stdout=StringIO())
        self.assertFalse(User.objects.exists())

    def test_creates_hashed_admin_that_can_log_in(self):
        self.bootstrap()
        user = User.objects.get(username='platform-owner')
        self.assertTrue(user.is_superuser and user.is_staff and user.is_active)
        self.assertNotEqual(user.password, self.values['DJANGO_SUPERUSER_PASSWORD'])
        self.assertTrue(self.client.login(username=user.username, password=self.values['DJANGO_SUPERUSER_PASSWORD']))
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_repeat_does_not_reset_password(self):
        self.bootstrap()
        self.bootstrap(DJANGO_SUPERUSER_PASSWORD='Different!Password9384')
        self.assertTrue(User.objects.get().check_password(self.values['DJANGO_SUPERUSER_PASSWORD']))
        self.assertEqual(User.objects.count(), 1)

    def test_existing_account_never_promoted_or_email_reused(self):
        user = User.objects.create_user('platform-owner', email='owner@example.com', password='existing')
        with self.assertRaises(CommandError):
            self.bootstrap()
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)
        with self.assertRaises(CommandError):
            self.bootstrap(DJANGO_SUPERUSER_USERNAME='another-owner')
        self.assertEqual(User.objects.count(), 1)

    def test_missing_or_weak_password_creates_nothing(self):
        for password in ('', 'short'):
            with self.subTest(password=password), self.assertRaises(CommandError):
                self.bootstrap(DJANGO_SUPERUSER_PASSWORD=password)
        self.assertFalse(User.objects.exists())

    @override_settings(NEON_AUTH_BASE_URL='https://auth.example.com/auth')
    def test_neon_auth_requires_explicit_link(self):
        self.bootstrap()
        self.assertFalse(User.objects.exists())
