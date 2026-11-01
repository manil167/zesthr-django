import hashlib
import hmac
import json

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.debug import sensitive_variables

from core.services.whatsapp import receive


@csrf_exempt  # Meta uses an HMAC signature, never browser session authentication.
@require_http_methods(['GET', 'POST'])
@sensitive_variables('body', 'signature', 'expected')
def webhook(request):
    if request.method == 'GET':
        configured = settings.WHATSAPP_VERIFY_TOKEN
        supplied = request.GET.get('hub.verify_token', '')
        if configured and request.GET.get('hub.mode') == 'subscribe' and hmac.compare_digest(supplied, configured):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponse(status=403)
    if not settings.WHATSAPP_APP_SECRET or not settings.WHATSAPP_PHONE_NUMBER_ID:
        return HttpResponse(status=503)
    try:
        content_length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        return HttpResponse(status=400)
    if content_length > 262144:
        return HttpResponse(status=413)
    body = request.body
    if len(body) > 262144:
        return HttpResponse(status=413)
    expected = 'sha256=' + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not hmac.compare_digest(signature, expected):
        return HttpResponse(status=403)
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or data.get('object') != 'whatsapp_business_account':
            return HttpResponse(status=400)
        messages = []
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                if str(value.get('metadata', {}).get('phone_number_id')) != settings.WHATSAPP_PHONE_NUMBER_ID:
                    continue
                for message in value.get('messages', []):
                    if message.get('type') == 'text':
                        if not isinstance(message.get('text', {}).get('body'), str) or not isinstance(message.get('from'), str):
                            raise ValueError
                        messages.append(message)
        if len(messages) > 100:
            raise ValueError
    except (ValueError, TypeError, AttributeError, UnicodeDecodeError):
        return HttpResponse(status=400)
    for message in messages:
        receive(message)
    return JsonResponse({'status': 'ok'})
