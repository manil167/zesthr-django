from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse
from .test_permissions import BaseTest


class AccountOperatorBoundaryTests(BaseTest):
    def setUp(self):
        self.sysadmin.is_staff = True
        self.sysadmin.save()
        self.login(self.sysadmin)

    def test_cannot_edit_or_reset_privileged_accounts(self):
        for user in (self.superuser, self.hr_admin, self.sysadmin):
            self.assertEqual(self.client.get(reverse("admin:auth_user_change", args=[user.pk])).status_code, 302)
            response = self.client.post(reverse("admin:auth_user_password_change", args=[user.pk]),
                                        {"password1": "New-Safe-Pass123!", "password2": "New-Safe-Pass123!"})
            self.assertIn(response.status_code, (302, 404))
            user.refresh_from_db()
            self.assertTrue(user.check_password("pw-12345-x"))

    def test_crafted_creation_cannot_grant_privilege(self):
        self.client.post(reverse("admin:auth_user_add"), {
            "username": "new.account", "password1": "New-Safe-Pass123!", "password2": "New-Safe-Pass123!",
            "is_superuser": "on", "is_staff": "on"})
        user = User.objects.get(username="new.account")
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)

    def test_account_permission_fields_are_readonly_for_operators(self):
        model_admin = admin.site._registry[User]
        request = RequestFactory().get("/")
        request.user = self.sysadmin
        self.assertTrue({"groups", "user_permissions", "is_superuser", "is_staff"}.issubset(
            model_admin.get_readonly_fields(request, self.alice_user)))
        self.alice_user.is_superuser = True
        with self.assertRaises(PermissionDenied):
            model_admin.save_model(request, self.alice_user, None, True)

    def test_accounts_are_deactivated_instead_of_deleted(self):
        model_admin = admin.site._registry[User]
        request = RequestFactory().get("/")
        request.user = self.superuser
        self.assertFalse(model_admin.has_delete_permission(request, self.alice_user))
