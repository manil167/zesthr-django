"""Readable account choices without changing stable login identifiers."""
from django.contrib.auth import get_user_model


def user_choice_label(user):
    if not user:
        return ''
    name = ' '.join(part for part in (user.first_name.strip(), user.last_name.strip()) if part)
    if not name:
        name = user.username if not user.username.startswith('neon_') else f'Account #{user.pk}'
    return f'{name} — {user.email}' if user.email else name


def label_user_choices(field):
    if field is not None and getattr(getattr(field, 'queryset', None), 'model', None) is get_user_model():
        field.label_from_instance = user_choice_label
        field.queryset = field.queryset.order_by('first_name', 'last_name', 'email', 'pk')
    return field
