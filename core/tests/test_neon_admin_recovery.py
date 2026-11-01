import os
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from core.auth_backends import provision
from core.models import NeonIdentity
from core.services.neon_auth import NeonAuthError


@override_settings(NEON_AUTH_BASE_URL='https://test.neonauth.example/neondb/auth',
                   NEON_AUTH_APP_ORIGIN='https://zesthr-django.onrender.com',
                   AUTHENTICATION_BACKENDS=['core.auth_backends.NeonBackend'])
class NeonAdminRecoveryTests(TestCase):
    config = {'DJANGO_BOOTSTRAP_ADMIN': 'True', 'NEON_ADMIN_USER_ID': 'exact-admin-subject',
              'NEON_ADMIN_EMAIL': 'owner@example.com'}

    def profile(self, **changes):
        return {'id': 'exact-admin-subject', 'email': 'owner@example.com',
                'emailVerified': True, 'name': 'Neon Owner', **changes}

    def test_exact_verified_identity_receives_persistent_admin_access(self):
        with patch.dict(os.environ, self.config):
            user = provision(self.profile())
        self.assertTrue(user.is_staff and user.is_superuser)
        self.assertFalse(user.has_usable_password())
        # Disabling the initial grant does not revoke the deliberately granted role.
        with patch.dict(os.environ, {'DJANGO_BOOTSTRAP_ADMIN': 'False'}):
            self.assertTrue(provision(self.profile()).is_superuser)

    def test_existing_neon_user_can_be_explicitly_promoted_on_next_login(self):
        with patch.dict(os.environ, {'DJANGO_BOOTSTRAP_ADMIN': 'False'}):
            user = provision(self.profile())
        self.assertFalse(user.is_superuser)
        with patch.dict(os.environ, self.config):
            self.assertEqual(provision(self.profile()).pk, user.pk)
        user.refresh_from_db()
        self.assertTrue(user.is_superuser)

    def test_email_only_subject_only_unverified_or_disabled_grants_nothing(self):
        for changes in ({'id':'wrong-subject'}, {'email':'someone@example.com'}, {'emailVerified':False}):
            with self.subTest(changes=changes), patch.dict(os.environ, self.config):
                user = provision(self.profile(**changes))
                self.assertFalse(user.is_superuser)
        with patch.dict(os.environ, {**self.config, 'DJANGO_BOOTSTRAP_ADMIN':'False'}):
            self.assertFalse(provision(self.profile()).is_superuser)

    def test_never_matches_existing_local_admin_by_email(self):
        local = User.objects.create_superuser('old-local-admin', email='owner@example.com', password='LocalOnly!12984')
        with patch.dict(os.environ, self.config):
            user = provision(self.profile())
        self.assertNotEqual(user.pk, local.pk)
        self.assertEqual(NeonIdentity.objects.get().user, user)
        self.assertTrue(User.objects.get(pk=local.pk).check_password('LocalOnly!12984'))

    def test_does_not_reactivate_disabled_neon_account(self):
        with patch.dict(os.environ, {'DJANGO_BOOTSTRAP_ADMIN':'False'}):
            user = provision(self.profile())
        user.is_active = False
        user.save()
        with patch.dict(os.environ, self.config):
            result = provision(self.profile())
        self.assertFalse(result.is_superuser or result.is_active)

    @patch('core.services.neon_auth.call')
    def test_verified_neon_login_can_open_actual_admin(self, call):
        call.side_effect = [({}, 'session_token=opaque'), ({'user':self.profile(), 'session':{'id':'s'}}, '')]
        with patch.dict(os.environ, self.config):
            response = self.client.post(reverse('login'), {'username':'owner@example.com', 'password':'ProviderPassword!923'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    @patch('core.services.neon_auth.call')
    def test_provider_reset_request_uses_configured_trusted_origin(self, call):
        call.return_value = ({'status': True}, '')
        response = self.client.post(reverse('password_reset'), {'email':'owner@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        call.assert_called_once_with('request-password-reset', payload={'email':'owner@example.com',
            'redirectTo':'https://zesthr-django.onrender.com/password/neon/reset/'})

    @patch('core.services.neon_auth.call')
    def test_provider_delivery_failure_is_visible_without_leaking_details(self, call):
        call.side_effect = NeonAuthError('sensitive upstream response', status_code=403, code='INVALID_ORIGIN')
        response = self.client.post(reverse('password_reset'), {'email':'owner@example.com'})
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, 'Password recovery is temporarily unavailable', status_code=503)
        self.assertNotIn('sensitive upstream response', response.content.decode())

    @patch('core.services.neon_auth.call')
    def test_unknown_account_response_does_not_enumerate_users(self, call):
        call.side_effect = NeonAuthError('not found', status_code=404, code='USER_NOT_FOUND')
        response = self.client.post(reverse('password_reset'), {'email':'absent@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))

    @patch('core.services.neon_auth.call')
    def test_real_reset_route_consumes_session_token_and_updates_provider(self, call):
        call.return_value = ({'status':True}, '')
        response = self.client.get(reverse('neon_password_reset') + '?token=single-use-test-token')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['neon_reset_token'], 'single-use-test-token')
        response = self.client.post(reverse('neon_password_reset'),
            {'password':'NewUniquePassword!4238','confirmation':'NewUniquePassword!4238'})
        self.assertRedirects(response, reverse('login'))
        call.assert_called_once_with('reset-password', payload={'token':'single-use-test-token', 'newPassword':'NewUniquePassword!4238'})
        self.assertNotIn('neon_reset_token', self.client.session)

    @patch('core.services.neon_auth.call')
    def test_missing_or_failed_link_never_calls_password_reset(self, call):
        for path in (reverse('neon_password_reset'), reverse('neon_password_reset') + '?error=INVALID_TOKEN'):
            response = self.client.get(path)
            self.assertContains(response, 'Request reset link')
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.client.post(reverse('neon_password_reset'), {'password':'Valid!Password2847','confirmation':'Valid!Password2847'})
        call.assert_not_called()
