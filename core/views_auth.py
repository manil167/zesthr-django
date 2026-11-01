from core.permissions import get_employee_profile
"""Sign-in, password change and password reset."""
from django.conf import settings
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from django.contrib import messages
from django.contrib.auth import views as auth
from django.urls import reverse_lazy
from django.views.generic.edit import FormView
from django.shortcuts import redirect, render

from .forms_auth import PublicSignupForm
from . import roles
from .models import SecurityEvent
from .services import accounts, neon_auth


class LoginView(auth.LoginView):
    template_name = "login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "").strip()
        if accounts.is_login_blocked(username, accounts.client_ip(request)):
            accounts.record_event(SecurityEvent.LOGIN_BLOCKED, request=request, username=username)
            form = self.get_form()
            form.is_bound = True
            form.add_error(None, f"Too many failed attempts. Please wait {settings.LOGIN_FAILURE_WINDOW_MINUTES} "
                                 "minutes, or use \"Forgot password\".")
            response = self.render_to_response(self.get_context_data(form=form))
            response.status_code = 429
            return response
        return super().post(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if neon_auth.enabled():
            form.fields['username'].label = 'Email address'
            form.error_messages['invalid_login'] = 'Enter a correct email address and password. Verify your email before signing in.'
            form.fields['username'].widget.attrs['autocomplete'] = 'email'
        return form

    def form_valid(self, form):
        response = super().form_valid(form)
        token = self.request.session.pop('pending_invitation', None)
        if token:
            from .forms_auth import accept_invitation
            from django.core.exceptions import ValidationError
            try:
                accept_invitation(token, self.request.user)
            except ValidationError:
                messages.warning(self.request, 'Your invitation could not be accepted. Ask HR for a new invitation.')
        return response

    def get_default_redirect_url(self):
        user = self.request.user

        if (
            user.has_perm(roles.VIEW_ALL_ATTENDANCE)
            or user.has_perm(roles.VIEW_EMPLOYEE)
            or get_employee_profile(user) is not None
        ):
            return str(reverse_lazy("dashboard"))

        return str(reverse_lazy("account_pending"))

   
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['neon_auth_enabled'] = neon_auth.enabled()
        context["google_login_enabled"] = bool(
            not neon_auth.enabled() and settings.GOOGLE_OAUTH_CLIENT_ID
            and settings.GOOGLE_OAUTH_CLIENT_SECRET
        )

        return context

class PasswordChangeView(auth.PasswordChangeView):
    template_name = "password_change.html"
    success_url = reverse_lazy("home")

    def get_form_class(self):
        if neon_auth.enabled():
            from .forms_auth import NeonPasswordChangeForm
            return NeonPasswordChangeForm
        return super().get_form_class()

    def get_context_data(self, **kwargs):
        return super().get_context_data(forced=accounts.must_change_password(self.request.user), **kwargs)

    def form_valid(self, form):
        if neon_auth.enabled():
            try:
                _, cookie = neon_auth.call('change-password', cookie=self.request.session.get('neon_cookie'),
                    payload={'currentPassword': form.cleaned_data['old_password'],
                             'newPassword': form.cleaned_data['new_password1'], 'revokeOtherSessions': True})
                if cookie:
                    self.request.session['neon_cookie'] = cookie
            except neon_auth.NeonAuthError as exc:
                form.add_error(None, str(exc))
                return self.form_invalid(form)
            response = redirect(self.success_url)
        else:
            response = super().form_valid(form)  # also keeps THIS session signed in
        accounts.mark_password_changed(self.request.user)
        accounts.record_event(SecurityEvent.PASSWORD_CHANGED, request=self.request, user=self.request.user)
        messages.success(self.request, "Your password has been changed. Other devices were signed out.")
        return response


class PasswordResetView(auth.PasswordResetView):
    template_name = "password_reset_form.html"
    email_template_name = "password_reset_email.txt"
    subject_template_name = "password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        email = form.cleaned_data["email"].strip()
        if accounts.is_password_reset_blocked(email, accounts.client_ip(self.request)):
            # Keep the normal response so this endpoint cannot disclose account state.
            return redirect(self.success_url)
        accounts.record_event(SecurityEvent.PASSWORD_RESET_REQUESTED, request=self.request,
                              username=email)
        if neon_auth.enabled():
            try:
                neon_auth.call('request-password-reset', payload={'email': email,
                    'redirectTo': settings.NEON_AUTH_APP_ORIGIN.rstrip('/') + str(reverse_lazy('neon_password_reset'))})
            except neon_auth.NeonAuthError as exc:
                if exc.code not in {'USER_NOT_FOUND', 'INVALID_EMAIL', 'EMAIL_NOT_VERIFIED'}:
                    form.add_error(None, 'Password recovery is temporarily unavailable. Ask your administrator to check Neon email delivery, the Auth URL and trusted application origin, then try again.')
                    response = self.form_invalid(form)
                    response.status_code = 503
                    return response
            return redirect(self.success_url)
        return super().form_valid(form)  # always says "if that address exists..." (no account enumeration)


class PasswordResetConfirmView(auth.PasswordResetConfirmView):
    template_name = "password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        accounts.mark_password_changed(self.user)
        accounts.record_event(SecurityEvent.PASSWORD_RESET_COMPLETED, request=self.request, user=self.user)
        return response

@method_decorator(sensitive_post_parameters('password1', 'password2'), name='dispatch')
class SignupView(FormView):
    template_name = "signup.html"
    form_class = PublicSignupForm
    success_url = reverse_lazy("login")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['neon_auth_enabled'] = neon_auth.enabled()

        context["google_login_enabled"] = bool(
            not neon_auth.enabled() and settings.GOOGLE_OAUTH_CLIENT_ID
            and settings.GOOGLE_OAUTH_CLIENT_SECRET
        )

        return context

    def get_form_class(self):
        if neon_auth.enabled():
            from .forms_auth import NeonSignupForm
            return NeonSignupForm
        return super().get_form_class()

    def get_initial(self):
        token = self.request.GET.get('invitation', '')
        if token and len(token) <= 256:
            self.request.session['pending_invitation'] = token
        return {**super().get_initial(), 'invitation': token}

    def form_valid(self, form):
        if neon_auth.enabled():
            try:
                neon_auth.call('sign-up/email', payload={'email': form.cleaned_data['email'],
                    'password': form.cleaned_data['password1'],
                    'name': form.cleaned_data['first_name'] + ' ' + form.cleaned_data['last_name'],
                    'callbackURL': self.request.build_absolute_uri(reverse_lazy('login'))})
            except neon_auth.NeonAuthError as exc:
                form.add_error(None, str(exc))
                return self.form_invalid(form)
            if form.cleaned_data.get('invitation'):
                self.request.session['pending_invitation'] = form.cleaned_data['invitation']
            self.request.session['neon_signup_email'] = form.cleaned_data['email']
            messages.success(self.request, 'Account created. Verify your email, then sign in. Organization access requires an HR invitation and linked employee profile.')
            return redirect('neon_verify_email')
        form.save()

        messages.success(
            self.request,
            (
                "Your HROS account has been created successfully. "
                "You can now sign in. HR authorization is required "
                "before you can access employee, attendance, or payroll information."
            ),
        )

        return super().form_valid(form)
    
def account_pending(request):
    """Show the authorization message to accounts not linked to an employee."""
    user = request.user

    # Authorized HR users and linked employees must retain normal access.
    if (
        user.has_perm(roles.VIEW_ALL_ATTENDANCE)
        or user.has_perm(roles.VIEW_EMPLOYEE)
        or get_employee_profile(user) is not None
    ):
        return redirect("dashboard")

    return render(request, "account_pending.html")


@never_cache
@sensitive_post_parameters('password', 'confirmation')
def neon_password_reset(request):
    from django import forms
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    if not neon_auth.enabled():
        return redirect('password_reset')
    class ResetForm(forms.Form):
        password = forms.CharField(widget=forms.PasswordInput, label='New password')
        confirmation = forms.CharField(widget=forms.PasswordInput, label='Confirm password')
        def clean(self):
            data = super().clean()
            if data.get('password') != data.get('confirmation'):
                raise ValidationError('Passwords do not match.')
            if data.get('password'):
                validate_password(data['password'])
            return data
    if request.GET.get('error'):
        request.session.pop('neon_reset_token', None)
    elif request.GET.get('token'):
        token = request.GET['token']
        if len(token) <= 2048:
            request.session['neon_reset_token'] = token
        else:
            request.session.pop('neon_reset_token', None)
        return redirect('neon_password_reset')
    if not request.session.get('neon_reset_token'):
        return render(request, 'neon_password_reset.html', {'valid_link': False})
    form = ResetForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            neon_auth.call('reset-password', payload={'token': request.session.get('neon_reset_token', ''),
                                                     'newPassword': form.cleaned_data['password']})
        except neon_auth.NeonAuthError as exc:
            form.add_error(None, str(exc))
        else:
            request.session.pop('neon_reset_token', None)
            messages.success(request, 'Password reset. Sign in with your new password.')
            return redirect('login')
    return render(request, 'neon_password_reset.html', {'form': form, 'valid_link': True})


class LogoutView(auth.LogoutView):
    def post(self, request, *args, **kwargs):
        cookie = request.session.get('neon_cookie')
        if neon_auth.enabled() and cookie:
            try:
                neon_auth.call('sign-out', cookie=cookie, payload={})
            except neon_auth.NeonAuthError:
                pass  # Local sign-out must still succeed during a provider outage.
        return super().post(request, *args, **kwargs)


@sensitive_post_parameters('password', 'password1', 'password2')
def neon_verify_email(request):
    from django import forms
    if not neon_auth.enabled():
        return redirect('login')
    class VerifyForm(forms.Form):
        email = forms.EmailField()
        code = forms.CharField(max_length=12, required=False)
    form = VerifyForm(request.POST or None, initial={'email': request.session.get('neon_signup_email', '')})
    if request.method == 'POST' and form.is_valid():
        from django.utils import timezone
        last = request.session.get('neon_verification_sent', 0)
        if request.POST.get('action') == 'resend' and timezone.now().timestamp() - last < 60:
            form.add_error(None, 'Please wait one minute before requesting another code.')
        else:
            try:
                if request.POST.get('action') == 'resend':
                    neon_auth.call('email-otp/send-verification-otp',
                        payload={'email': form.cleaned_data['email'], 'type': 'email-verification'})
                    request.session['neon_verification_sent'] = timezone.now().timestamp()
                    messages.success(request, 'If verification is available for this account, a code will arrive shortly.')
                elif not form.cleaned_data.get('code'):
                    form.add_error('code', 'Enter the code from your email.')
                else:
                    neon_auth.call('email-otp/verify-email', payload={'email': form.cleaned_data['email'], 'otp': form.cleaned_data['code']})
                    messages.success(request, 'Email verified. You can now sign in.')
                    return redirect('login')
            except neon_auth.NeonAuthError as exc:
                form.add_error(None, str(exc))
    return render(request, 'neon_verify_email.html', {'form': form})


@sensitive_post_parameters('password')
def neon_sdk_signup(request):
    """CSRF-protected same-origin signup endpoint for the Neon browser SDK."""
    import json
    from django.http import JsonResponse
    from .forms_auth import NeonSignupForm
    if request.method != 'POST':
        return JsonResponse({'code': 'METHOD_NOT_ALLOWED', 'message': 'POST required.'}, status=405)
    if not neon_auth.enabled():
        return JsonResponse({'code': 'NOT_CONFIGURED', 'message': 'Neon Auth is not configured.'}, status=503)
    if len(request.body) > 8192:
        return JsonResponse({'code': 'INVALID_INPUT', 'message': 'Request is too large.'}, status=400)
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip().split(' ', 1)
        payload = {'first_name': name[0], 'last_name': name[1] if len(name) > 1 else '-',
            'email': data.get('email'), 'password1': data.get('password'), 'password2': data.get('password'),
            'invitation': request.session.get('pending_invitation', '')}
    except (ValueError, AttributeError, TypeError, IndexError):
        return JsonResponse({'code': 'INVALID_INPUT', 'message': 'Invalid signup details.'}, status=400)
    form = NeonSignupForm(payload)
    if not form.is_valid():
        return JsonResponse({'code': 'INVALID_INPUT', 'message': 'Check your email, password and invitation.',
                             'errors': form.errors.get_json_data()}, status=400)
    try:
        result, _ = neon_auth.call('sign-up/email', payload={'email': payload['email'],
            'password': payload['password1'], 'name': data['name'],
            'callbackURL': request.build_absolute_uri(reverse_lazy('login'))})
    except neon_auth.NeonAuthError as exc:
        return JsonResponse({'code': 'SIGNUP_FAILED', 'message': str(exc)}, status=400)
    request.session['neon_signup_email'] = payload['email']
    user = result.get('user', {})
    # No provider session cookie or token is exposed in the browser response.
    return JsonResponse({'user': {'id': user.get('id'), 'email': user.get('email'),
        'name': user.get('name'), 'emailVerified': bool(user.get('emailVerified'))}})
