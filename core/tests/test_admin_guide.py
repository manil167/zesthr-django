from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse


class AdminGuideTests(TestCase):
    def test_requires_same_active_staff_access_as_admin_site(self):
        url = reverse('admin_guide')
        self.assertRedirects(self.client.get(url), '/admin/login/?next=' + url, fetch_redirect_response=False)
        for staff, active in [(False, True), (True, False)]:
            user = User.objects.create_user(f'user-{staff}', is_staff=staff, is_active=active)
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 302)
        staff = User.objects.create_user('staff-guide', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-cache', response['Cache-Control'])
        self.assertContains(response, 'No model sections have been assigned')

    def test_sections_and_pay_fields_follow_operator_permissions(self):
        staff = User.objects.create_user('hr-guide', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='core', codename='view_employee'))
        self.client.force_login(staff)
        response = self.client.get(reverse('admin_guide'))
        self.assertEqual(response.status_code, 200)
        sections = response.context['sections']
        self.assertEqual([s['object_name'] for s in sections], ['Employee'])
        self.assertNotIn('Basic salary', [f['label'] for f in sections[0]['fields']])
        self.assertNotContains(response, 'id="auth-user"')
        self.assertTrue(all(f['requirement'] == 'Read-only for your account' for f in sections[0]['fields']))

    def test_platform_admin_gets_complete_registered_reference_and_help_links(self):
        user = User.objects.create_superuser('guide-owner', 'owner@example.com', 'test-password')
        self.client.force_login(user)
        response = self.client.get(reverse('admin_guide'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'EMP-001')
        self.assertContains(response, 'Africa/Lagos')
        self.assertContains(response, 'Monthly accrual')
        self.assertContains(response, 'id="auth-user"')
        user_section = next(s for s in response.context['sections'] if s['anchor'] == 'auth-user')
        self.assertIn('First name', [f['label'] for f in user_section['fields']])
        self.assertIn('Staff status', [f['label'] for f in user_section['fields']])
        self.assertContains(response, 'id="organizations-organizationmembership"')
        self.assertContains(response, 'Read-only / automatic')
        self.assertContains(self.client.get(reverse('admin:index')), reverse('admin_guide'))
        self.assertContains(self.client.get('/notifications/'), reverse('admin_guide'))
        self.assertEqual(self.client.post(reverse('admin_guide')).status_code, 405)

    def test_nonstaff_navigation_does_not_advertise_restricted_guide(self):
        self.client.force_login(User.objects.create_user('employee-guide'))
        self.assertNotContains(self.client.get('/notifications/'), reverse('admin_guide'))
