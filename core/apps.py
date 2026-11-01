from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_roles(sender, **kwargs):
    from .roles import sync_roles

    sync_roles()
    from .models import EmploymentType, default_organization
    for name in ("Full-time", "Part-time", "Contract"):
        EmploymentType.objects.get_or_create(name=name, organization=default_organization())


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from . import signals  # noqa: F401  (connects the sign-in audit receivers)

        # Keep role groups in step with core/roles.py after every `migrate`.
        post_migrate.connect(_sync_roles, sender=self)
