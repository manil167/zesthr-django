"""Conversation orchestration and database-backed usage limits."""
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.models import AssistantQuota
from apps.organizations.tenancy import organization_ids_for
from .assistant import answer
from .assistant_ai import OpenAIProvider

MAX_QUESTION = 2000
MAX_MESSAGES = 12
SESSION_KEY = "hros_assistant_history"


class AssistantRateLimit(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


def availability(user):
    if not settings.ASSISTANT_ENABLED:
        return {'mode': 'guide', 'reason': 'disabled'}
    if not settings.OPENAI_API_KEY:
        return {'mode': 'guide', 'reason': 'missing_key'}
    authorized = user.is_active and (user.is_superuser or bool(organization_ids_for(user)))
    return {'mode': 'ai', 'reason': ''} if authorized else {'mode': 'guide', 'reason': 'membership_required'}


def mode(user):
    return availability(user)['mode']


def history(session, user):
    if session.get("hros_assistant_owner") != str(user.pk):
        return []
    items = session.get(SESSION_KEY, [])
    if not isinstance(items, list):
        return []
    return [{"role": item["role"], "content": item["content"][:6000]}
            for item in items[-MAX_MESSAGES:]
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)]


def clear(session):
    session.pop(SESSION_KEY, None)
    session.pop("hros_assistant_owner", None)


@transaction.atomic
def consume_quota(user):
    now = timezone.now()
    minute = now.replace(second=0, microsecond=0)
    # Serialize across web workers using the existing parent user row.
    get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
    quota, _ = AssistantQuota.objects.get_or_create(user=user, defaults={"minute": minute, "day": now.date()})
    if quota.minute != minute:
        quota.minute, quota.minute_count = minute, 0
    if quota.day != now.date():
        quota.day, quota.day_count = now.date(), 0
    if quota.day_count >= settings.ASSISTANT_DAILY_LIMIT:
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        raise AssistantRateLimit(max(1, int((tomorrow - now).total_seconds())))
    if quota.minute_count >= settings.ASSISTANT_MINUTE_LIMIT:
        raise AssistantRateLimit(max(1, 60 - now.second))
    quota.minute_count += 1
    quota.day_count += 1
    quota.save()


def reply(user, session, question, *, provider=None):
    consume_quota(user)
    previous = history(session, user)
    current_mode = mode(user)
    if current_mode == "ai":
        text = (provider or OpenAIProvider()).complete(previous[-8:] + [{"role": "user", "content": question}])
        result = {"answer": text, "url": "", "mode": "ai"}
    else:
        result = {**answer(question), "mode": "guide"}
    session[SESSION_KEY] = (previous + [{"role": "user", "content": question},
                                     {"role": "assistant", "content": result["answer"]}])[-MAX_MESSAGES:]
    session["hros_assistant_owner"] = str(user.pk)
    return {**result, **availability(user)}
