from django import template
from core.account_labels import user_choice_label

register = template.Library()
register.filter('account_label', user_choice_label)


@register.filter
def first_name_label(user):
    """A friendly label without exposing generated provider usernames."""
    name = (getattr(user, 'first_name', '') or '').strip()
    if name:
        return name.split()[0]
    username = getattr(user, 'username', '') or ''
    return 'Account' if username.startswith('neon_') or not username else username
