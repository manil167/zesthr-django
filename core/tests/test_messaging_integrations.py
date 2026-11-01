import hashlib
import hmac
import json
from datetime import timedelta, time
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.organizations.models import Organization, OrganizationMembership, Company, Branch
from apps.organizations.tenancy import organization_ids_for, scope_model_queryset
from core.forms import EmployeeForm
from core.forms_auth import PublicSignupForm, accept_invitation
from core.models import (Announcement, Attendance, Employee, Notification, WhatsAppLink,
    WhatsAppOutbox, WhatsAppReceipt, WorkSchedule, WorkLocation, EmploymentType, JobPosition,
    Department, Holiday, NeonIdentity, AttendanceAudit)
from core.services import messaging, whatsapp, neon_auth
from core.services.schedules import ScheduleResolver
from .test_permissions import BaseTest, make_user


class StrictTenancyTests(BaseTest):
    def setUp(self):
        self.other = Organization.objects.create(name='Other tenant', code='OTHER')
        self.foreign_user = User.objects.create_user('foreign', password='not-used')
        self.foreign = Employee.objects.create(name='Private foreign name', user=self.foreign_user,
                                               organization=self.other, basic_salary=100)

    def test_no_single_tenant_fallback_and_memberships_revoke_immediately(self):
        self.assertEqual(list(organization_ids_for(self.nobody)), [])
        self.assertIn(self.alice.organization_id, organization_ids_for(self.hr_admin))
        OrganizationMembership.objects.filter(user=self.hr_admin).update(is_active=False)
        self.assertEqual(list(organization_ids_for(self.hr_admin)), [])
        self.login(self.hr_admin)
        self.assertEqual(self.client.get(reverse('employee_edit', args=[self.alice.pk])).status_code, 404)

    def test_inactive_organization_revokes_self_service_and_api(self):
        self.alice.organization.is_active = False
        self.alice.organization.save()
        self.login(self.alice_user)
        self.assertEqual(self.client.post(reverse('attendance_today'), {'action': 'check_in'}).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/payroll/').json()['results'], [])

    def test_every_reference_choice_is_owned_by_organization(self):
        foreign = [Company.objects.create(organization=self.other, name='Private company', code='C'),
            WorkLocation.objects.create(organization=self.other, name='Private location'),
            EmploymentType.objects.create(organization=self.other, name='Private employment'),
            WorkSchedule.objects.create(organization=self.other, name='Private schedule', start_time=time(9), end_time=time(17)),
            JobPosition.objects.create(organization=self.other, title='Private position'),
            Department.objects.create(organization=self.other, name='Private department', code='D')]
        form = EmployeeForm(user=self.hr_admin, can_link_account=True)
        for item in foreign:
            self.assertFalse(scope_model_queryset(type(item).objects.filter(pk=item.pk), self.hr_admin).exists())
        self.assertNotIn(self.foreign_user, form.fields['user'].queryset)
        self.assertNotIn(self.nobody, form.fields['user'].queryset)

    def test_employee_save_rejects_foreign_company_and_reference_records(self):
        company = Company.objects.create(organization=self.other, name='Foreign Co', code='F')
        self.alice.company = company
        with self.assertRaises(ValidationError):
            self.alice.save()
        self.alice.company = None
        self.alice.work_location = WorkLocation.objects.create(organization=self.other, name='Foreign office')
        with self.assertRaises(ValidationError):
            self.alice.save()

    def test_branch_must_belong_to_selected_company(self):
        first = Company.objects.create(organization=self.alice.organization, name='One', code='ONE')
        second = Company.objects.create(organization=self.alice.organization, name='Two', code='TWO')
        self.alice.company = first
        self.alice.branch = Branch.objects.create(company=second, name='Wrong company', code='BR')
        with self.assertRaises(ValidationError):
            self.alice.save()

    def test_tenant_names_and_default_schedules_do_not_collide(self):
        for org in [self.alice.organization, self.other]:
            WorkSchedule.objects.create(organization=org, name='Standard', is_default=True,
                                        start_time=time(9), end_time=time(17))
            Department.objects.create(organization=org, name='Finance', code='FIN')
        resolver = ScheduleResolver()
        self.assertEqual(resolver.for_employee(self.alice).organization, self.alice.organization)
        self.assertEqual(resolver.for_employee(self.foreign).organization, self.other)

    def test_hr_user_admin_hides_foreign_and_unassigned_users(self):
        from core.admin import HROSUserAdmin
        from django.contrib.admin import site
        from django.test import RequestFactory
        request = RequestFactory().get('/admin/auth/user/')
        request.user = self.hr_admin
        users = HROSUserAdmin(User, site).get_queryset(request)
        self.assertNotIn(self.foreign_user, users)
        self.assertNotIn(self.nobody, users)

    def test_admin_filters_do_not_reveal_foreign_organization(self):
        self.hr_admin.is_staff = True
        self.hr_admin.save()
        self.login(self.hr_admin)
        response = self.client.get('/admin/core/employee/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.other.name)
        self.assertNotContains(response, self.foreign.name)

    def test_foreign_holidays_hidden(self):
        Holiday.objects.create(organization=self.other, name='Foreign private holiday', date=timezone.localdate())
        self.login(self.alice_user)
        self.assertNotContains(self.client.get(reverse('holidays')), 'Foreign private holiday')


class AnnouncementsAndInvitationTests(BaseTest):
    def setUp(self):
        StrictTenancyTests.setUp(self)

    def test_broadcast_delivered_to_every_active_member_of_selected_tenant_only(self):
        outsider = self.foreign_user
        messaging.announce(user=self.hr_admin, organization=self.alice.organization, title='News', message='Our team only')
        recipients = set(Notification.objects.filter(notification_type=Notification.ANNOUNCEMENT).values_list('recipient_id', flat=True))
        expected = set(OrganizationMembership.objects.filter(organization=self.alice.organization,
            is_active=True, user__is_active=True).values_list('user_id', flat=True))
        self.assertEqual(recipients, expected)
        self.assertNotIn(outsider.pk, recipients)
        self.assertNotIn(self.nobody.pk, recipients)

    def test_foreign_announcement_and_employee_broadcast_are_denied(self):
        with self.assertRaises(PermissionDenied):
            messaging.announce(user=self.hr_admin, organization=self.other, title='No', message='No')
        with self.assertRaises(PermissionDenied):
            messaging.announce(user=self.alice_user, organization=self.alice.organization, title='No', message='No')
        self.assertEqual(Announcement.objects.count(), 0)
        self.login(self.hr_admin)
        response = self.client.post(reverse('announcement_create'), {'organization': self.other.pk, 'title': 'No', 'message': 'No'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('organization', response.context['form'].errors)

    def test_revoked_member_cannot_read_prior_tenant_announcement(self):
        messaging.announce(user=self.hr_admin, organization=self.alice.organization, title='Internal plan', message='Private')
        OrganizationMembership.objects.filter(user=self.alice_user).update(is_active=False)
        self.login(self.alice_user)
        self.assertNotContains(self.client.get(reverse('notifications')), 'Internal plan')

    def signup_data(self, token, email='invite@example.com'):
        return {'username': 'invited', 'first_name': 'New', 'last_name': 'Person', 'email': email,
                'password1': 'Th1sIsALong!TestPassword', 'password2': 'Th1sIsALong!TestPassword', 'invitation': token}

    def test_invitation_is_email_bound_expiring_single_use_and_grants_no_hr_role(self):
        token = messaging.invite(user=self.hr_admin, organization=self.alice.organization, email='invite@example.com')
        bad = PublicSignupForm(self.signup_data(token, 'attacker@example.com'))
        self.assertFalse(bad.is_valid())
        form = PublicSignupForm(self.signup_data(token))
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(list(organization_ids_for(user)), [self.alice.organization_id])
        self.assertFalse(user.is_staff)
        self.assertFalse(user.has_perm('core.send_announcement'))
        with self.assertRaises(ValidationError):
            accept_invitation(token, user)

    def test_uninvited_signup_has_no_membership(self):
        form = PublicSignupForm(self.signup_data(''))
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(list(organization_ids_for(user)), [])

    def test_guidance_requires_login_and_renders_no_employee_data(self):
        response = self.client.post(reverse('app_assistant'), json.dumps({'question': 'punch in'}), content_type='application/json')
        self.assertEqual(response.status_code, 302)
        self.login(self.alice_user)
        response = self.client.post(reverse('app_assistant'), json.dumps({'question': 'punch in'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Punch Attendance', response.json()['answer'])
        self.assertNotIn(self.foreign.name, response.content.decode())
        self.assertEqual(self.client.post(reverse('app_assistant'), '{}', content_type='application/json').status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.alice_user)
        self.assertEqual(csrf_client.post(reverse('app_assistant'), '{}', content_type='application/json').status_code, 403)


@override_settings(WHATSAPP_APP_SECRET='test-signature-secret', WHATSAPP_VERIFY_TOKEN='test-verify',
                   WHATSAPP_PHONE_NUMBER_ID='123456', WHATSAPP_ACCESS_TOKEN='test-only')
class WhatsAppTests(BaseTest):
    phone = '2348012345678'

    def post_message(self, text, message_id='wamid.1', valid=True, phone_id='123456'):
        body = json.dumps({'object': 'whatsapp_business_account', 'entry': [{'changes': [{'value': {
            'metadata': {'phone_number_id': phone_id}, 'messages': [{'id': message_id, 'from': self.phone,
            'type': 'text', 'text': {'body': text}}]}}]}]}).encode()
        signature = 'sha256=' + hmac.new(b'test-signature-secret', body, hashlib.sha256).hexdigest()
        return self.client.post(reverse('whatsapp_webhook'), body, content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature if valid else 'sha256=forged')

    def connect(self):
        token = messaging.create_link_token(self.alice_user)
        self.assertEqual(self.post_message('LINK ' + token, 'link.1').status_code, 200)
        self.assertEqual(WhatsAppLink.objects.get(user=self.alice_user).phone, self.phone)

    def test_verification_and_signature_are_required(self):
        self.assertEqual(self.client.get(reverse('whatsapp_webhook'), {'hub.mode': 'subscribe',
            'hub.verify_token': 'test-verify', 'hub.challenge': 'challenge'}).content, b'challenge')
        self.assertEqual(self.client.get(reverse('whatsapp_webhook')).status_code, 403)
        self.assertEqual(self.post_message('IN', valid=False).status_code, 403)
        self.assertEqual(WhatsAppReceipt.objects.count(), 0)

    def test_linked_punch_is_audited_and_duplicate_event_is_idempotent(self):
        self.connect()
        for _ in range(2):
            self.assertEqual(self.post_message('IN', 'in.1').status_code, 200)
        self.assertEqual(Attendance.objects.filter(emp=self.alice, check_in__isnull=False).count(), 1)
        self.assertEqual(WhatsAppReceipt.objects.filter(message_id='in.1').count(), 1)
        self.assertEqual(AttendanceAudit.objects.filter(emp=self.alice, action='CHECK_IN').count(), 1)

    def test_unlinked_number_cannot_punch(self):
        self.post_message('IN')
        self.assertFalse(Attendance.objects.filter(emp=self.alice, date=timezone.localdate()).exists())
        self.assertIn('Link your account', WhatsAppOutbox.objects.get().text)

    def test_expired_link_and_replay_are_rejected(self):
        token = messaging.create_link_token(self.alice_user)
        WhatsAppLink.objects.filter(user=self.alice_user).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.post_message('LINK ' + token)
        self.assertIsNone(WhatsAppLink.objects.get(user=self.alice_user).phone)
        self.connect()
        self.post_message('LINK ' + token, 'replay')
        self.assertIn('Unable', WhatsAppOutbox.objects.latest('pk').text)

    def test_revoked_membership_and_deactivated_employee_cannot_punch(self):
        self.connect()
        OrganizationMembership.objects.filter(user=self.alice_user).update(is_active=False)
        self.post_message('IN', 'revoked')
        self.assertIn('unavailable', WhatsAppOutbox.objects.latest('pk').text)
        self.assertFalse(Attendance.objects.filter(emp=self.alice, date=timezone.localdate()).exists())

    def test_wrong_business_number_and_delivery_status_are_ignored(self):
        self.post_message('IN', phone_id='another-business')
        self.assertEqual(WhatsAppReceipt.objects.count(), 0)

    def test_notification_command_does_not_expose_another_organization(self):
        self.connect()
        other = Organization.objects.create(name='Foreign', code='FOREIGN')
        Notification.objects.create(recipient=self.alice_user, organization=other,
            notification_type=Notification.ANNOUNCEMENT, title='Secret', message='Hidden')
        self.post_message('NOTIFICATIONS', 'notifications')
        self.assertNotIn('Hidden', WhatsAppOutbox.objects.latest('pk').text)

    @patch('core.services.whatsapp.requests.post')
    def test_delivery_retries_without_repeating_attendance(self, post):
        self.connect()
        self.post_message('IN', 'in.1')
        post.return_value.status_code = 429
        self.assertEqual(whatsapp.deliver(), 0)
        self.assertEqual(Attendance.objects.filter(emp=self.alice, check_in__isnull=False).count(), 1)
        self.assertTrue(WhatsAppOutbox.objects.filter(last_error='http_429', attempts=1).exists())
        WhatsAppOutbox.objects.update(next_attempt_at=timezone.now())
        post.return_value.status_code = 200
        self.assertEqual(whatsapp.deliver(), 2)
        self.assertEqual(WhatsAppOutbox.objects.filter(sent_at__isnull=False).count(), 2)

    @patch('core.services.whatsapp.requests.post')
    def test_delivery_cancels_after_access_revocation(self, post):
        self.connect()
        OrganizationMembership.objects.filter(user=self.alice_user).update(is_active=False)
        whatsapp.deliver()
        post.assert_not_called()
        self.assertTrue(WhatsAppOutbox.objects.filter(last_error='access_revoked').exists())


@override_settings(NEON_AUTH_BASE_URL='https://test.neonauth.example/neondb/auth', NEON_AUTH_APP_ORIGIN='https://app.example')
class NeonAuthTests(TestCase):
    def profile(self, **kwargs):
        return {'id': 'provider-user-123', 'email': 'verified@example.com', 'emailVerified': True, 'name': 'Test User', **kwargs}

    @patch('core.services.neon_auth.call')
    def test_provider_identity_never_takes_over_existing_account_by_email(self, call):
        from core.auth_backends import NeonBackend
        existing = User.objects.create_superuser('admin', email='verified@example.com', password='local-password')
        call.side_effect = [({}, '__Secure-neonauth.session_token=test'), ({'user': self.profile(), 'session': {'id': 's'}}, '')]
        request = Mock(session={})
        user = NeonBackend().authenticate(request, username='verified@example.com', password='remote-password')
        self.assertNotEqual(user.pk, existing.pk)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(list(organization_ids_for(user)), [])
        self.assertIn('neon_cookie', request.session)

    @patch('core.services.neon_auth.call')
    def test_unverified_email_is_refused(self, call):
        from core.auth_backends import NeonBackend
        call.side_effect = [({}, 'session_token=test'), ({'user': self.profile(emailVerified=False), 'session': {'id': 's'}}, '')]
        self.assertIsNone(NeonBackend().authenticate(Mock(session={}), username='a@example.com', password='pw'))
        self.assertEqual(NeonIdentity.objects.count(), 0)

    @patch('core.services.neon_auth.requests.request')
    def test_rest_client_uses_tls_timeout_and_no_redirects(self, request):
        response = Mock(status_code=200, cookies=[])
        response.json.return_value = {'user': self.profile()}
        request.return_value = response
        neon_auth.call('sign-up/email', payload={'email': 'a@example.com'})
        args, kwargs = request.call_args
        self.assertEqual(args[1], 'https://test.neonauth.example/neondb/auth/sign-up/email')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], 10)
        self.assertNotIn('verify', kwargs)

    @patch('core.services.neon_auth.call')
    def test_middleware_logs_out_on_revoked_remote_session(self, call):
        from core.auth_backends import provision
        from core.middleware import NeonSessionMiddleware
        user = provision(self.profile())
        request = Mock(user=user, session=Mock())
        request.session.get.side_effect = lambda key, default=None: 'core.auth_backends.NeonBackend' if key == '_auth_user_backend' else 'invalid'
        call.side_effect = neon_auth.NeonAuthError('revoked')
        with patch('django.contrib.auth.logout') as logout:
            response = NeonSessionMiddleware(Mock())(request)
        logout.assert_called_once_with(request)
        self.assertEqual(response.status_code, 302)

    def test_neon_database_pooling_requires_encrypted_connections(self):
        from payroll_project.settings.env import postgres_database
        from django.core.exceptions import ImproperlyConfigured
        config = postgres_database('postgresql://user:password@ep-test-pooler.example/db')
        self.assertEqual(config['CONN_MAX_AGE'], 0)
        self.assertIsNone(config['OPTIONS']['prepare_threshold'])
        self.assertEqual(config['OPTIONS']['sslmode'], 'verify-full')
        with self.assertRaises(ImproperlyConfigured):
            postgres_database('postgresql://user:password@ep-test.example/db?sslmode=disable')


class DatabaseTenantGuardTests(BaseTest):
    def setUp(self):
        StrictTenancyTests.setUp(self)

    def test_bulk_update_cannot_move_employee_or_reference_ownership(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=self.alice.pk).update(organization=self.other)
        company = Company.objects.create(organization=self.alice.organization, name='Local company', code='LOCAL')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Company.objects.filter(pk=company.pk).update(organization=self.other)

    def test_bulk_employee_and_department_relations_cannot_cross_tenants(self):
        schedule = WorkSchedule.objects.create(organization=self.other, name='Foreign shift', start_time=time(9), end_time=time(17))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Employee.objects.filter(pk=self.alice.pk).update(work_schedule=schedule)
        department = Department.objects.create(organization=self.alice.organization, name='Local dept', code='LD')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Department.objects.filter(pk=department.pk).update(work_schedule=schedule)


class NeonStorageConfigurationTests(TestCase):
    def test_neon_s3_credentials_wire_private_path_style_storage(self):
        import os
        from pathlib import Path
        from payroll_project.settings.document_storage import private_document_storage
        with patch.dict(os.environ, {'AWS_ENDPOINT_URL_S3': 'https://storage.example.com', 'AWS_REGION': 'us-east-2'}, clear=True):
            options = private_document_storage(Path('/tmp'))['OPTIONS']
        self.assertEqual(options['bucket_name'], 'assets')
        self.assertEqual(options['addressing_style'], 'path')
        self.assertTrue(options['querystring_auth'])
        self.assertTrue(options['verify'])
        self.assertNotIn('SSEKMSKeyId', options['object_parameters'])
        self.assertIsNone(options['default_acl'])


@override_settings(NEON_AUTH_BASE_URL='https://test.neonauth.example/neondb/auth')
class NeonSignupEndpointTests(TestCase):
    @patch('core.services.neon_auth.call')
    def test_sdk_signup_keeps_provider_token_server_side_and_user_unprivileged(self, call):
        call.return_value = ({'user': {'id': 'remote-id', 'email': 'person@example.com', 'emailVerified': False},
                              'token': 'must-not-expose'}, 'session_token=must-not-expose')
        response = self.client.post(reverse('neon_sdk_signup'), json.dumps({'email': 'person@example.com',
            'password': 'ALongTestPassword!234', 'name': 'Test Person'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['id'], 'remote-id')
        self.assertNotIn('must-not-expose', response.content.decode())
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(self.client.session['neon_signup_email'], 'person@example.com')

    def test_sdk_signup_requires_csrf_and_rejects_weak_passwords(self):
        response = self.client.post(reverse('neon_sdk_signup'), json.dumps({'email': 'person@example.com',
            'password': 'short', 'name': 'Test Person'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse('neon_sdk_signup'), '{}', content_type='application/json').status_code, 403)

    @patch('core.services.neon_auth.call')
    def test_neon_logout_revokes_provider_session(self, call):
        from core.views_auth import LogoutView
        from django.test import RequestFactory
        from django.contrib.sessions.middleware import SessionMiddleware
        request = RequestFactory().post('/logout/')
        request.session = Mock()
        request.session.get.return_value = 'session_token=private'
        with patch('django.contrib.auth.views.LogoutView.post') as parent:
            LogoutView().post(request)
        call.assert_called_once_with('sign-out', cookie='session_token=private', payload={})
        parent.assert_called_once()
