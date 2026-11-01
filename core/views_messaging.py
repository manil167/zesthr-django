import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables

from apps.organizations.models import Organization
from apps.organizations.tenancy import scope_queryset
from core.models import WhatsAppLink
from core.permissions import permission_required
from core.services import messaging
from core.services import assistant_chat
from core.services.assistant_ai import AssistantUnavailable


class OrganizationForm(forms.Form):
    organization = forms.ModelChoiceField(queryset=Organization.objects.none())

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['organization'].queryset = scope_queryset(
            Organization.objects.filter(is_active=True), user, 'pk')


class AnnouncementForm(OrganizationForm):
    title = forms.CharField(max_length=150)
    message = forms.CharField(max_length=4000, widget=forms.Textarea)


class InvitationForm(OrganizationForm):
    email = forms.EmailField()


@permission_required('core.send_announcement')
def announcement(request):
    form = AnnouncementForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        messaging.announce(user=request.user, **form.cleaned_data)
        messages.success(request, 'Announcement delivered to all active organization members in their notifications.')
        return redirect('notifications')
    return render(request, 'form.html', {'form': form, 'title': 'Send organization announcement'})


@never_cache
@permission_required('core.link_employee_account')
def invitation(request):
    form = InvitationForm(request.POST or None, user=request.user)
    invite_url = None
    if request.method == 'POST' and form.is_valid():
        token = messaging.invite(user=request.user, **form.cleaned_data)
        invite_url = request.build_absolute_uri(reverse('signup') + '?invitation=' + token)
    return render(request, 'invitation.html', {'form': form, 'invite_url': invite_url})


@never_cache
@login_required
def whatsapp_link(request):
    messaging.employee_for_bot(request.user)
    token = None
    if request.method == 'POST':
        if request.POST.get('action') == 'disconnect':
            WhatsAppLink.objects.filter(user=request.user).delete()
        else:
            messaging.employee_for_bot(request.user)
            token = messaging.create_link_token(request.user)
    link = WhatsAppLink.objects.filter(user=request.user).first()
    from django.conf import settings
    return render(request, 'whatsapp_link.html', {'link': link, 'token': token,
        'bot_number': settings.WHATSAPP_DISPLAY_NUMBER})


@never_cache
@login_required
@require_http_methods(["GET", "POST", "DELETE"])
@sensitive_variables("data", "question")
def assistant(request):
    if request.method == "GET":
        return JsonResponse({"messages": assistant_chat.history(request.session, request.user),
                             **assistant_chat.availability(request.user)})
    if request.method == "DELETE":
        assistant_chat.clear(request.session)
        return JsonResponse({"messages": [], **assistant_chat.availability(request.user)})
    if len(request.body) > 8192:
        return JsonResponse({"error": "Message is too long."}, status=400)
    try:
        data = json.loads(request.body)
        question = data.get("question") if isinstance(data, dict) else None
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= assistant_chat.MAX_QUESTION:
            raise ValueError
    except (ValueError, UnicodeDecodeError, RecursionError):
        return JsonResponse({"error": "Enter a message of up to 2000 characters."}, status=400)
    try:
        return JsonResponse(assistant_chat.reply(request.user, request.session, question.strip()))
    except assistant_chat.AssistantRateLimit as exc:
        return JsonResponse({"error": "Message limit reached. Please try again later."}, status=429,
                            headers={"Retry-After": str(exc.retry_after)})
    except AssistantUnavailable:
        return JsonResponse({"error": "The AI assistant is temporarily unavailable. Please try again shortly or contact HR."}, status=503)
