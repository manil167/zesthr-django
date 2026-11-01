from django.db import IntegrityError, transaction
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Branch, Company, Division, Organization, Team
from .models import OrganizationMembership
from .tenancy import scope_queryset


class OrganizationStructureTests(TestCase):
    def test_company_scoped_codes_and_hierarchy(self):
        organization = Organization.objects.create(name="Acme Group", code="ACME")
        company = Company.objects.create(organization=organization, name="Acme Nigeria", code="NG")
        branch = Branch.objects.create(company=company, name="Lagos", code="LOS")
        division = Division.objects.create(company=company, name="Operations", code="OPS")
        team = Team.objects.create(division=division, name="Support", code="SUP")

        self.assertEqual((branch.company, team.division.company), (company, company))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Company.objects.create(organization=organization, name="Duplicate", code="NG")


class TenantScopeTests(TestCase):
    def test_membership_limits_organization_owned_records(self):
        first = Organization.objects.create(name="First", code="FIRST")
        second = Organization.objects.create(name="Second", code="SECOND")
        user = User.objects.create_user("tenant-user")
        OrganizationMembership.objects.create(user=user, organization=first)

        self.assertEqual(list(scope_queryset(Organization.objects.all(), user, "pk")), [first])
        self.assertEqual(
            set(scope_queryset(Organization.objects.all(), User.objects.create_superuser("root"), "pk")),
            set(Organization.objects.all()),
        )
