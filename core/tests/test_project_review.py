from datetime import date, time
from django.contrib import admin
from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.contrib.sites.models import Site
from apps.organizations.models import Organization, OrganizationMembership
from core.account_labels import user_choice_label
from core.forms import EmployeeForm
from core.models import Attendance, Department, Employee, JobPosition, WorkSchedule
from core.services import attendance, employees, reports


class ProjectReviewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Review A', code='REVIEW_A')
        self.other = Organization.objects.create(name='Review B', code='REVIEW_B')
        self.operator = User.objects.create_user('operator', is_staff=True)
        OrganizationMembership.objects.create(user=self.operator, organization=self.org)
        self.person = User.objects.create_user('neon_internal', first_name='Ada', last_name='Okafor', email='ada@example.com')
        self.foreign_user = User.objects.create_user('neon_foreign', first_name='Private foreign', email='private@example.com')
        OrganizationMembership.objects.create(user=self.person, organization=self.org)
        OrganizationMembership.objects.create(user=self.foreign_user, organization=self.other)
        self.local = Employee.objects.create(organization=self.org, name='Local employee', designation='Analyst', basic_salary=1000)
        self.foreign = Employee.objects.create(organization=self.other, name='Foreign employee', designation='Analyst', basic_salary=2000)

    def test_user_choices_are_readable_and_remain_tenant_scoped(self):
        form = EmployeeForm(user=self.operator, can_link_account=True)
        field = form.fields['user']
        self.assertIn('Ada Okafor — ada@example.com', [label for value, label in field.choices])
        self.assertNotIn(self.foreign_user.pk, field.queryset.values_list('pk', flat=True))
        request = RequestFactory().get('/admin/core/employee/add/')
        request.user = self.operator
        field = admin.site.get_model_admin(Employee).formfield_for_foreignkey(Employee._meta.get_field('user'), request)
        self.assertEqual(field.label_from_instance(self.person), 'Ada Okafor — ada@example.com')
        self.assertNotIn(self.foreign_user.pk, field.queryset.values_list('pk', flat=True))
        self.assertNotIn('neon_', user_choice_label(User(pk=99, username='neon_unknown', email='other@example.com')))
        self.assertEqual(user_choice_label(None), '')

    def test_legacy_identity_and_site_configuration_are_platform_only_even_with_model_permissions(self):
        self.client.force_login(self.operator)
        for model in (Site, EmailAddress, SocialApp, SocialAccount, SocialToken):
            opts = model._meta
            self.operator.user_permissions.add(*Permission.objects.filter(content_type__app_label=opts.app_label,
                                                                         content_type__model=opts.model_name))
            url = reverse(f'admin:{opts.app_label}_{opts.model_name}_changelist')
            self.assertEqual(self.client.get(url).status_code, 403, opts.label)
        self.assertNotContains(self.client.get(reverse('admin_guide')), 'id="socialaccount-socialtoken"')

    def test_service_writes_cannot_target_foreign_employee(self):
        self.operator.user_permissions.add(Permission.objects.get(codename='deactivate_employee'))
        for action in (lambda: attendance.check_in(self.foreign, by=self.operator),
                       lambda: attendance.check_out(self.foreign, by=self.operator),
                       lambda: attendance.mark_status(self.foreign, 'A', by=self.operator),
                       lambda: attendance.save_manual(self.foreign, date(2026, 1, 1), status='A', by=self.operator, reason='review'),
                       lambda: employees.deactivate_employee(self.foreign, status='INACTIVE', by=self.operator),
                       lambda: employees.reactivate_employee(self.foreign, by=self.operator)):
            with self.assertRaises(PermissionDenied):
                action()
        self.assertFalse(Attendance.objects.filter(emp=self.foreign).exists())
        self.foreign.refresh_from_db()
        self.assertTrue(self.foreign.is_active)
        employees.deactivate_employee(self.local, status='INACTIVE', by=self.operator)
        employees.reactivate_employee(self.local, by=self.operator)
        self.local.refresh_from_db()
        self.assertTrue(self.local.is_active)

    def test_reference_relations_and_summary_enforce_tenant_boundaries(self):
        shift = WorkSchedule.objects.create(organization=self.other, name='Foreign shift', start_time=time(9), end_time=time(17))
        with self.assertRaises(ValidationError):
            Department.objects.create(organization=self.org, name='Invalid', code='BAD', work_schedule=shift)
        dept = Department.objects.create(organization=self.other, name='Foreign dept', code='F')
        with self.assertRaises(ValidationError):
            JobPosition.objects.create(organization=self.org, title='Invalid', department=dept)
        rows = reports.employee_summary(date(2026, 1, 1), date(2026, 1, 31), user=self.operator)
        self.assertEqual(list(rows), [self.local])
        OrganizationMembership.objects.filter(user=self.operator).update(is_active=False)
        self.assertFalse(reports.employee_summary(date(2026, 1, 1), date(2026, 1, 31), user=self.operator).exists())
