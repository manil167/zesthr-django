from django.conf import settings
from django.db import migrations


def assign_existing_users_to_default_organization(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Membership = apps.get_model("organizations", "OrganizationMembership")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    organization = Organization.objects.get(code="DEFAULT")
    for user_id in User.objects.values_list("id", flat=True):
        Membership.objects.get_or_create(user_id=user_id, organization=organization)


class Migration(migrations.Migration):
    dependencies = [("organizations", "0002_organizationmembership"), ("core", "0019_department_organization_employee_organization")]

    operations = [migrations.RunPython(assign_existing_users_to_default_organization, migrations.RunPython.noop)]
