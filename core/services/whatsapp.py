"""Signed inbound commands; network delivery runs separately from webhook handling."""
from datetime import timedelta

import requests
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from core import timeutil
from core.models import WhatsAppLink, WhatsAppOutbox, WhatsAppReceipt
from core.services import attendance, messaging
from core.services.assistant import answer

HELP = ('Commands: IN, OUT, STATUS, NOTIFICATIONS, HELP <question>, UNLINK. '
        'Link your account first from the website’s WhatsApp connection page. '
        'HR can send organization announcements from Notifications on the website.')


@transaction.atomic
def receive(message):
    message_id = message.get('id', '')
    phone = message.get('from', '')
    text = message.get('text', {}).get('body', '').strip()
    if not isinstance(message_id, str) or not message_id or len(message_id) > 255 or not isinstance(phone, str) or not phone.isdigit() or not 7 <= len(phone) <= 15:
        return
    # A unique receipt and attendance/outbox changes commit together. Meta retries are harmless.
    _, created = WhatsAppReceipt.objects.get_or_create(message_id=message_id)
    if not created:
        return
    link = WhatsAppLink.objects.select_for_update().select_related('user').filter(phone=phone).first()
    command = text.split(' ', 1)[0].upper()
    user = link.user if link else None
    if command == 'LINK':
        token = text.partition(' ')[2].strip()
        target = WhatsAppLink.objects.select_for_update().select_related('user').filter(
            token_hash=messaging.digest(token), expires_at__gt=timezone.now()).first() if token else None
        reply = 'Unable to connect. Generate a new code on the website and try again.'
        if target and (not link or target.pk == link.pk):
            try:
                messaging.employee_for_bot(target.user)
                with transaction.atomic():
                    target.phone, target.token_hash, target.expires_at = phone, '', None
                    target.linked_at = timezone.now()
                    target.save()
                user = target.user
                reply = 'WhatsApp connected. ' + HELP
            except (PermissionDenied, IntegrityError):
                pass
    elif not link:
        reply = HELP
    elif command == 'UNLINK':
        link.delete()
        user = None  # Only the non-sensitive disconnect acknowledgement remains deliverable.
        reply = 'WhatsApp disconnected. Use the website to connect again.'
    else:
        try:
            employee = messaging.employee_for_bot(user)
            if command in ('IN', 'OUT'):
                function = attendance.check_in if command == 'IN' else attendance.check_out
                record = function(employee, by=user)
                moment = record.check_in if command == 'IN' else record.check_out
                reply = f'Punch {command.lower()} recorded for {record.date}: {moment:%H:%M} ({timeutil.zone_for(employee)}).'
            elif command == 'STATUS':
                record = attendance.current_record(employee, timeutil.today_for(employee))
                reply = ('No attendance recorded for your current shift.' if not record else
                         f'{record.date}: {record.get_status_display()}. In: {record.check_in or "—"}. Out: {record.check_out or "—"}.')
            elif command == 'NOTIFICATIONS':
                items = list(messaging.visible_notifications(user).filter(
                    organization=employee.organization)[:5])
                reply = '\n\n'.join(f'{item.title}: {item.message}' for item in items) or 'No organization notifications.'
            elif command == 'HELP' and ' ' in text:
                reply = answer(text.partition(' ')[2])['answer']
            else:
                reply = HELP
        except PermissionDenied:
            reply = 'Your employee access is unavailable. Sign in on the website or contact HR.'
        except attendance.AttendanceError as exc:
            reply = str(exc)
    WhatsAppOutbox.objects.create(phone=phone, user=user, text=reply[:4000], next_attempt_at=timezone.now())


def deliver(limit=50):
    """At-least-once response delivery, bounded retries; requires a scheduled worker."""
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise ValueError('Configure WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID before sending.')
    sent = 0
    # Row locking keeps concurrent PostgreSQL workers from sending the same row.
    for _ in range(limit):
        with transaction.atomic():
            from django.db import connection
            queryset = WhatsAppOutbox.objects.filter(sent_at__isnull=True, attempts__lt=8,
                next_attempt_at__lte=timezone.now()).order_by('pk')
            queryset = queryset.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else queryset.select_for_update()
            item = queryset.first()
            if not item:
                break
            if item.user_id:
                if not WhatsAppLink.objects.filter(user_id=item.user_id, phone=item.phone).exists():
                    item.attempts, item.last_error = 8, 'link_revoked'
                    item.save(update_fields=['attempts', 'last_error'])
                    continue
                try:
                    messaging.employee_for_bot(item.user)
                except PermissionDenied:
                    item.attempts, item.last_error = 8, 'access_revoked'
                    item.save(update_fields=['attempts', 'last_error'])
                    continue
            item.attempts += 1
            # Replies are session messages. Never attempt an unapproved proactive template.
            if item.created_at < timezone.now() - timedelta(hours=23):
                item.attempts, item.last_error = 8, 'reply_window_expired'
                item.save(update_fields=['attempts', 'last_error'])
                continue
            try:
                response = requests.post(
                    f'https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages',
                    headers={'Authorization': 'Bearer ' + settings.WHATSAPP_ACCESS_TOKEN},
                    json={'messaging_product': 'whatsapp', 'to': item.phone,
                          'type': 'text', 'text': {'body': item.text}}, timeout=10, allow_redirects=False)
                if 200 <= response.status_code < 300:
                    item.sent_at, item.last_error = timezone.now(), ''
                    sent += 1
                else:
                    item.last_error = f'http_{response.status_code}'
                    if response.status_code in (400, 401, 403, 404):
                        item.attempts = 8
            except requests.RequestException:
                item.last_error = 'network_error'
            item.next_attempt_at = timezone.now() + timedelta(seconds=min(3600, 30 * 2 ** item.attempts))
            item.save()
    return sent
