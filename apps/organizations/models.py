"""Organization structure reference data.

These models are intentionally independent of legacy employee records. Employee
assignment is added in the subsequent, data-migrated organization-scope step.
"""
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


code_validator = RegexValidator(r"^[A-Z0-9_-]+$", "Use uppercase letters, numbers, hyphens or underscores.")


class Organization(models.Model):
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=30, unique=True, validators=[code_validator])
    default_time_zone = models.CharField(max_length=64, default="UTC")
    default_currency = models.CharField(max_length=3, default="USD")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    """Explicit tenant scope for users who may operate on an organization."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_memberships")
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="memberships")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "organization"], name="user_organization_membership_unique")]

    def __str__(self):
        return f"{self.user} @ {self.organization}"


class Company(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="companies")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30, validators=[code_validator])
    country = models.CharField(max_length=2, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["organization__name", "name"]
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="org_company_code_unique")]

    def __str__(self):
        return f"{self.organization}: {self.name}"


class Branch(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30, validators=[code_validator])
    city = models.CharField(max_length=100, blank=True)
    time_zone = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__name", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="company_branch_code_unique")]

    def __str__(self):
        return f"{self.company}: {self.name}"


class Division(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="divisions")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30, validators=[code_validator])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__name", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="company_division_code_unique")]

    def __str__(self):
        return f"{self.company}: {self.name}"


class Team(models.Model):
    division = models.ForeignKey(Division, on_delete=models.PROTECT, related_name="teams")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30, validators=[code_validator])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["division__name", "name"]
        constraints = [models.UniqueConstraint(fields=["division", "code"], name="division_team_code_unique")]

    def __str__(self):
        return f"{self.division}: {self.name}"


class JobGrade(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="job_grades")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, validators=[code_validator])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__name", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="company_grade_code_unique")]

    def __str__(self):
        return f"{self.company}: {self.name}"


class CostCenter(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="cost_centers")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, validators=[code_validator])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__name", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="company_costcenter_code_unique")]

    def __str__(self):
        return f"{self.company}: {self.name}"
