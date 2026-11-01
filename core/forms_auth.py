
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError


User = get_user_model()


class PublicSignupForm(UserCreationForm):
    invitation = forms.CharField(required=False, widget=forms.HiddenInput)
    """
    Public registration form.

    New accounts receive no staff or administrator privileges.
    HR authorization is handled separately.
    """

    first_name = forms.CharField(
        max_length=150,
        required=True,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "given-name",
                "placeholder": "First name",
            }
        ),
    )

    last_name = forms.CharField(
        max_length=150,
        required=True,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "family-name",
                "placeholder": "Last name",
            }
        ),
    )

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "placeholder": "Email address",
            }
        ),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
        )

    def clean(self):
        data = super().clean()
        if data.get('invitation') and data.get('email'):
            validate_invitation(data['invitation'], data['email'])
        return data

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        existing_user = User.objects.filter(
            email__iexact=email
        ).exists()

        if existing_user:
            raise ValidationError(
                "An account with this email address already exists."
            )

        return email

    def save(self, commit=True):
        user = super().save(commit=False)

        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.email = self.cleaned_data["email"].strip().lower()

        # Public registration must never grant elevated privileges.
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True

        if commit:
            from django.db import transaction
            with transaction.atomic():
                user.save()
                if self.cleaned_data.get('invitation'):
                    accept_invitation(self.cleaned_data['invitation'], user)
        return user


def validate_invitation(token, email):
    from django.utils import timezone
    from core.models import OrganizationInvitation
    from core.services.messaging import digest
    invitation = OrganizationInvitation.objects.filter(token_hash=digest(token),
        email__iexact=email, accepted_at__isnull=True, expires_at__gt=timezone.now(),
        organization__is_active=True).first()
    if not invitation:
        raise ValidationError('This invitation is invalid, expired, or belongs to another email address.')
    return invitation


def accept_invitation(token, user):
    from django.db import transaction
    from django.utils import timezone
    from apps.organizations.models import OrganizationMembership
    from core.models import OrganizationInvitation
    with transaction.atomic():
        invitation = validate_invitation(token, user.email)
        invitation = OrganizationInvitation.objects.select_for_update().get(pk=invitation.pk)
        if invitation.accepted_at:
            raise ValidationError('This invitation has already been accepted.')
        OrganizationMembership.objects.get_or_create(user=user, organization=invitation.organization)
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=['accepted_at'])


class NeonSignupForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(label='Password', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirm password', widget=forms.PasswordInput)
    invitation = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean(self):
        data = super().clean()
        if data.get('password1') != data.get('password2'):
            self.add_error('password2', 'Passwords do not match.')
        from django.contrib.auth.password_validation import validate_password
        if data.get('password1'):
            validate_password(data['password1'])
        if data.get('invitation') and data.get('email'):
            validate_invitation(data['invitation'], data['email'])
        return data


class NeonPasswordChangeForm(forms.Form):
    old_password = forms.CharField(widget=forms.PasswordInput)
    new_password1 = forms.CharField(widget=forms.PasswordInput)
    new_password2 = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, user=None, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        if data.get('new_password1') != data.get('new_password2'):
            self.add_error('new_password2', 'Passwords do not match.')
        from django.contrib.auth.password_validation import validate_password
        if data.get('new_password1'):
            validate_password(data['new_password1'], self.user)
        return data
