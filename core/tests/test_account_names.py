from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import TestCase, override_settings
from core.auth_backends import provision, sync_profile_name
from core.templatetags.account_names import first_name_label


@override_settings(NEON_AUTH_BASE_URL='https://names.neonauth.example/auth')
class AccountNameTests(TestCase):
    def test_identity_name_refresh_preserves_account(self):
        profile = {'id': 'candidate-1', 'email': 'candidate@example.com', 'name': 'Ada Lovelace'}
        user = provision(profile)
        self.assertEqual((user.first_name, user.last_name), ('Ada', 'Lovelace'))
        updated = provision({**profile, 'name': 'Grace Hopper'})
        self.assertEqual((updated.pk, updated.username), (user.pk, user.username))
        self.assertEqual((updated.first_name, updated.last_name), ('Grace', 'Hopper'))
        self.assertFalse(updated.is_staff or updated.is_superuser)

    def test_missing_names_and_legacy_fallback(self):
        user = User.objects.create_user('neon_generated', first_name='Ada Lovelace')
        sync_profile_name(user, {'name': None})
        user.refresh_from_db()
        self.assertEqual(first_name_label(user), 'Ada')
        user.first_name = ''
        self.assertEqual(first_name_label(user), 'Account')
        self.assertEqual(first_name_label(User(username='legacy')), 'legacy')

    def test_headers_show_first_name_and_escape_it(self):
        user = User.objects.create_user('neon_generated', first_name='<Ada> Lovelace')
        html = Template('{% load account_names %}{{ user|first_name_label }}').render(Context({'user': user}))
        self.assertEqual(html, '&lt;Ada&gt;')
        self.client.force_login(user)
        for url in ['/', '/notifications/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '&lt;Ada&gt;')
            self.assertNotContains(response, 'neon_generated')

    def test_existing_session_refreshes_name_after_identity_validation(self):
        from unittest.mock import patch
        from django.test import RequestFactory
        from django.http import HttpResponse
        from core.middleware import NeonSessionMiddleware
        profile = {'id': 'existing-session', 'email': 'candidate@example.com', 'name': 'Ada Lovelace'}
        user = provision(profile)
        request = RequestFactory().get('/notifications/')
        request.user = user
        request.session = {'_auth_user_backend': 'core.auth_backends.NeonBackend', 'neon_cookie': 'test-cookie'}
        with patch('core.services.neon_auth.session_user', return_value={**profile, 'name': 'Grace Hopper'}):
            response = NeonSessionMiddleware(lambda request: HttpResponse('ok'))(request)
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Grace')
