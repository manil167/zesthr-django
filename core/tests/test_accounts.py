import re
from datetime import timedelta

from django.core import mail
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import roles
from core.models import AccountSecurity, Employee, SecurityEvent
from core.services import accounts, employees as employee_service

from .test_permissions import BaseTest, make_user
from .test_settings import run_check

PW = "pw-12345-x"
NEW_PW = "N3w-Str0ng-Passw0rd!"
LOGIN = lambda name, pw=PW: {"username": name, "password": pw}  # noqa: E731


def events(event_type, **kw):
    return SecurityEvent.objects.filter(event_type=event_type, **kw)


class SignInAuditTests(BaseTest):
    def test_successful_login_and_logout_are_recorded(self):
        r = self.client.post(reverse("login"), LOGIN("alice"), REMOTE_ADDR="203.0.113.7", HTTP_USER_AGENT="TestBrowser/1")
        self.assertEqual(r.status_code, 302)
        ev = events("LOGIN_SUCCESS").get()
        self.assertEqual((ev.user, ev.username, ev.ip_address, ev.user_agent), (self.alice_user, "alice", "203.0.113.7", "TestBrowser/1"))
        self.client.post(reverse("logout"))
        self.assertEqual(events("LOGOUT").count(), 1)

    def test_failed_login_is_recorded_without_the_password(self):
        r = self.client.post(reverse("login"), LOGIN("alice", "SuperSecretWrong1"))
        self.assertEqual(r.status_code, 200)
        ev = events("LOGIN_FAILED").get()
        self.assertEqual(ev.username, "alice")
        for e in SecurityEvent.objects.all():
            self.assertNotIn("SuperSecretWrong1", f"{e.username}{e.detail}{e.user_agent}")

    def test_unknown_user_failure_is_recorded_too(self):
        self.client.post(reverse("login"), LOGIN("ghost"))
        self.assertEqual(events("LOGIN_FAILED", username="ghost").count(), 1)

    def test_logout_requires_post(self):
        self.login(self.alice_user)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)

    def test_events_cannot_be_modified(self):
        ev = accounts.record_event("LOGOUT", username="x")
        ev.detail = "tampered"
        with self.assertRaises(Exception):
            ev.save()

    def test_client_ip_ignores_forged_header_unless_trusted(self):
        req = RequestFactory().get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="1.1.1.1, 198.51.100.9")
        self.assertEqual(accounts.client_ip(req), "10.0.0.5")
        with override_settings(TRUST_X_FORWARDED_FOR=True):
            self.assertEqual(accounts.client_ip(req), "198.51.100.9")  # hop added by our own proxy


class LockoutTests(BaseTest):
    def fail(self, name="alice", n=1, ip="198.51.100.1"):
        for _ in range(n):
            self.client.post(reverse("login"), LOGIN(name, "wrong-password"), REMOTE_ADDR=ip)

    def test_blocked_after_repeated_failures_even_with_the_right_password(self):
        self.fail(n=5)
        r = self.client.post(reverse("login"), LOGIN("alice"), REMOTE_ADDR="198.51.100.1")
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "Too many failed attempts", status_code=429)
        self.assertEqual(events("LOGIN_BLOCKED").count(), 1)
        self.assertEqual(events("LOGIN_SUCCESS").count(), 0)

    def test_four_failures_are_still_allowed(self):
        self.fail(n=4)
        self.assertEqual(self.client.post(reverse("login"), LOGIN("alice"), REMOTE_ADDR="198.51.100.1").status_code, 302)

    def test_block_is_per_name_and_per_address(self):
        self.fail(n=5)
        self.assertEqual(self.client.post(reverse("login"), LOGIN("bob"), REMOTE_ADDR="198.51.100.1").status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("login"), LOGIN("alice"), REMOTE_ADDR="198.51.100.99").status_code, 302)

    def test_same_message_for_unknown_users_so_nothing_leaks(self):
        self.fail("ghost", n=5)
        r = self.client.post(reverse("login"), LOGIN("ghost"), REMOTE_ADDR="198.51.100.1")
        self.assertContains(r, "Too many failed attempts", status_code=429)

    def test_block_is_case_insensitive_and_expires(self):
        self.fail("Alice", n=5)
        self.assertEqual(self.client.post(reverse("login"), LOGIN("ALICE"), REMOTE_ADDR="198.51.100.1").status_code, 429)
        SecurityEvent.objects.all()._raw_delete(using="default")  # the ORM refuses; this stands in for the window passing
        self.assertEqual(self.client.post(reverse("login"), LOGIN("alice"), REMOTE_ADDR="198.51.100.1").status_code, 302)

    def test_a_successful_login_resets_the_counter(self):
        self.fail(n=4)
        self.client.post(reverse("login"), LOGIN("alice"), REMOTE_ADDR="198.51.100.1")
        self.client.post(reverse("logout"))
        self.fail(n=4)
        self.assertEqual(self.client.post(reverse("login"), LOGIN("alice"), REMOTE_ADDR="198.51.100.1").status_code, 302)

    def test_old_failures_outside_the_window_do_not_count(self):
        self.fail(n=5)
        old = timezone.now() - timedelta(minutes=16)
        from django.db import connection
        with connection.cursor() as cur:  # events are immutable through the ORM; age them directly
            cur.execute("UPDATE security_event SET created_at = %s", [old])
        self.assertEqual(self.client.post(reverse("login"), LOGIN("alice"), REMOTE_ADDR="198.51.100.1").status_code, 302)


class LoginPageTests(BaseTest):
    def test_demo_credentials_are_hidden_in_production(self):
        self.assertNotContains(self.client.get(reverse("login")), "admin123")

    @override_settings(DEBUG=True)
    def test_demo_credentials_are_also_hidden_in_development(self):
        self.assertNotContains(self.client.get(reverse("login")), "admin123")

    def test_landing_page_depends_on_what_the_account_can_use(self):
        for user, target in ((self.hr_admin, "dashboard"), (self.alice_user, "dashboard"), (self.sysadmin, "account_pending")):
            self.client.logout()
            r = self.client.post(reverse("login"), LOGIN(user.username))
            self.assertRedirects(r, reverse(target), fetch_redirect_response=False)

    def test_next_is_honoured_only_for_this_site(self):
        r = self.client.post(reverse("login") + "?next=/holidays/", LOGIN("alice"))
        self.assertRedirects(r, "/holidays/", fetch_redirect_response=False)
        self.client.logout()
        r = self.client.post(reverse("login") + "?next=https://evil.example.org/", LOGIN("alice"))
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)

    def test_inactive_account_cannot_sign_in_and_gets_the_generic_message(self):
        self.alice_user.is_active = False
        self.alice_user.save()
        r = self.client.post(reverse("login"), LOGIN("alice"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "inactive")

    def test_existing_session_stops_working_when_account_is_deactivated(self):
        self.login(self.alice_user)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        accounts.set_account_active(self.alice_user, False)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)


class PasswordChangeTests(BaseTest):
    def change(self, old=PW, new=NEW_PW, confirm=None):
        return self.client.post(reverse("password_change"), {
            "old_password": old, "new_password1": new, "new_password2": confirm or new})

    def test_requires_login(self):
        self.assertEqual(self.client.get(reverse("password_change")).status_code, 302)

    def test_changes_password_logs_event_and_keeps_current_session(self):
        self.login(self.alice_user)
        r = self.change()
        self.assertEqual(r.status_code, 302)
        self.alice_user.refresh_from_db()
        self.assertTrue(self.alice_user.check_password(NEW_PW))
        self.assertEqual(events("PASSWORD_CHANGED", user=self.alice_user).count(), 1)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)  # still signed in here

    def test_other_devices_are_signed_out(self):
        self.login(self.alice_user)
        other = self.client_class()
        other.force_login(self.alice_user)
        self.assertEqual(other.get(reverse("dashboard")).status_code, 200)
        self.change()
        self.assertEqual(other.get(reverse("dashboard")).status_code, 302)

    def test_wrong_old_password_and_weak_new_passwords_are_rejected(self):
        self.login(self.alice_user)
        for kwargs in ({"old": "wrong"}, {"new": "short1!"}, {"new": "password123"}, {"new": "1234567890123"},
                       {"new": NEW_PW, "confirm": "different-Passw0rd!"}):
            self.assertEqual(self.change(**kwargs).status_code, 200, kwargs)
        self.alice_user.refresh_from_db()
        self.assertTrue(self.alice_user.check_password(PW))


class ProvisionedAccountTests(BaseTest):
    def provisioned(self):
        u = make_user("newhire", roles.EMPLOYEE)
        accounts.require_password_change(u, by=self.hr_admin)
        return u

    def test_flagged_user_is_sent_to_change_password_everywhere(self):
        self.login(self.provisioned())
        for name in ("dashboard", "holidays", "leaves_list", "home"):
            r = self.client.get(reverse(name))
            self.assertRedirects(r, reverse("password_change"), fetch_redirect_response=False, msg_prefix=name)

    def test_flagged_user_may_still_open_the_change_page_and_sign_out(self):
        self.login(self.provisioned())
        self.assertContains(self.client.get(reverse("password_change")), "requires a new password")
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)

    def test_changing_the_password_clears_the_requirement(self):
        user = self.provisioned()
        self.login(user)
        self.client.post(reverse("password_change"), {"old_password": PW, "new_password1": NEW_PW, "new_password2": NEW_PW})
        self.assertFalse(AccountSecurity.objects.get(user=user).must_change_password)
        self.assertIsNotNone(AccountSecurity.objects.get(user=user).password_changed_at)
        self.assertEqual(self.client.get(reverse("holidays")).status_code, 200)

    def test_users_created_in_the_admin_are_provisioned_but_existing_ones_are_not(self):
        self.login(self.superuser)
        self.client.post(reverse("admin:auth_user_add"), {
            "username": "admin.made", "password1": NEW_PW, "password2": NEW_PW})
        self.assertTrue(AccountSecurity.objects.get(user__username="admin.made").must_change_password)
        self.assertFalse(AccountSecurity.objects.filter(user=self.alice_user).exists())
        self.assertEqual(events("PASSWORD_CHANGE_REQUIRED").count(), 1)

    def test_admin_actions_require_change_and_deactivate_without_locking_yourself_out(self):
        self.login(self.superuser)
        url = reverse("admin:auth_user_changelist")
        self.client.post(url, {"action": "require_password_change", "_selected_action": [self.alice_user.pk]})
        self.assertTrue(AccountSecurity.objects.get(user=self.alice_user).must_change_password)
        self.client.post(url, {"action": "deactivate_accounts", "_selected_action": [self.alice_user.pk, self.superuser.pk]})
        self.alice_user.refresh_from_db(), self.superuser.refresh_from_db()
        self.assertFalse(self.alice_user.is_active)
        self.assertTrue(self.superuser.is_active)
        self.assertEqual(events("ACCOUNT_DEACTIVATED", user=self.alice_user).count(), 1)
        self.client.post(url, {"action": "activate_accounts", "_selected_action": [self.alice_user.pk]})
        self.alice_user.refresh_from_db()
        self.assertTrue(self.alice_user.is_active)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetTests(BaseTest):
    def setUp(self):
        self.alice_user.email = "alice@example.com"
        self.alice_user.save()

    def request_reset(self, email="alice@example.com"):
        return self.client.post(reverse("password_reset"), {"email": email})

    def link(self):
        return re.search(r"https?://[^/]+(/\S+)", mail.outbox[-1].body).group(1)

    def test_known_address_gets_one_email_with_a_working_link(self):
        r = self.request_reset()
        self.assertRedirects(r, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alice@example.com"])
        self.assertNotIn(PW, mail.outbox[0].body)
        self.assertEqual(events("PASSWORD_RESET_REQUESTED").count(), 1)

    def test_unknown_and_inactive_addresses_get_the_same_answer_and_no_email(self):
        self.alice_user.is_active = False
        self.alice_user.save()
        for email in ("nobody@example.com", "alice@example.com"):
            self.assertRedirects(self.request_reset(email), reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(PASSWORD_RESET_MAX_REQUESTS=2, PASSWORD_RESET_WINDOW_MINUTES=15)
    def test_reset_requests_are_rate_limited_without_changing_the_response(self):
        first = self.request_reset()
        second = self.request_reset()
        limited = self.request_reset()

        self.assertRedirects(first, reverse("password_reset_done"))
        self.assertRedirects(second, reverse("password_reset_done"))
        self.assertRedirects(limited, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(events("PASSWORD_RESET_REQUESTED").count(), 2)

    def test_full_reset_flow_and_the_link_works_only_once(self):
        self.request_reset()
        link = self.link()
        r = self.client.get(link, follow=True)  # Django swaps the token for a session
        form_url = r.redirect_chain[-1][0]
        r = self.client.post(form_url, {"new_password1": NEW_PW, "new_password2": NEW_PW})
        self.assertRedirects(r, reverse("password_reset_complete"))
        self.alice_user.refresh_from_db()
        self.assertTrue(self.alice_user.check_password(NEW_PW))
        self.assertEqual(events("PASSWORD_RESET_COMPLETED", user=self.alice_user).count(), 1)
        self.assertContains(self.client_class().get(link, follow=True), "Link not valid")

    def test_reset_clears_a_forced_change_requirement(self):
        accounts.require_password_change(self.alice_user)
        self.request_reset()
        form_url = self.client.get(self.link(), follow=True).redirect_chain[-1][0]
        self.client.post(form_url, {"new_password1": NEW_PW, "new_password2": NEW_PW})
        self.assertFalse(AccountSecurity.objects.get(user=self.alice_user).must_change_password)

    def test_expired_link_is_refused(self):
        self.request_reset()
        link = self.link()
        with override_settings(PASSWORD_RESET_TIMEOUT=-1):
            self.assertContains(self.client.get(link, follow=True), "Link not valid")

    def test_weak_password_is_refused_on_reset(self):
        self.request_reset()
        form_url = self.client.get(self.link(), follow=True).redirect_chain[-1][0]
        r = self.client.post(form_url, {"new_password1": "password123", "new_password2": "password123"})
        self.assertEqual(r.status_code, 200)
        self.alice_user.refresh_from_db()
        self.assertTrue(self.alice_user.check_password(PW))


class SecurityLogAccessTests(BaseTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for u in (cls.sysadmin, cls.hr_admin):
            u.is_staff = True
            u.save()

    def test_only_system_administrators_and_superusers_can_read_the_log(self):
        accounts.record_event("LOGOUT", username="x")
        url = reverse("admin:core_securityevent_changelist")
        for user, code in ((self.sysadmin, 200), (self.superuser, 200), (self.hr_admin, 403)):
            self.login(user)
            self.assertEqual(self.client.get(url).status_code, code, user.username)

    def test_nobody_can_add_edit_or_delete_entries_in_the_admin(self):
        ev = accounts.record_event("LOGOUT", username="x")
        self.login(self.superuser)
        self.assertEqual(self.client.get(reverse("admin:core_securityevent_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:core_securityevent_delete", args=[ev.pk]), {"post": "yes"}).status_code, 403)
        self.assertTrue(SecurityEvent.objects.filter(pk=ev.pk).exists())

    def test_system_administrator_role_holds_the_permission_others_do_not(self):
        self.assertTrue(self.sysadmin.has_perm(roles.VIEW_SECURITY_LOG))
        for user in (self.hr_admin, self.hr_officer, self.payroll_officer, self.alice_user):
            self.assertFalse(user.has_perm(roles.VIEW_SECURITY_LOG), user.username)


class LifecycleIntegrationTests(BaseTest):
    def test_deactivating_an_employee_logs_the_account_change(self):
        employee_service.deactivate_employee(self.alice, status="INACTIVE")
        self.assertEqual(events("ACCOUNT_DEACTIVATED", user=self.alice_user).count(), 1)
        employee_service.reactivate_employee(Employee.objects.get(pk=self.alice.pk))
        self.assertEqual(events("ACCOUNT_ACTIVATED", user=self.alice_user).count(), 1)


class ConfigurationTests(SimpleTestCase):
    def test_session_policy(self):
        from django.conf import settings
        self.assertEqual(settings.SESSION_COOKIE_AGE, settings.SESSION_IDLE_MINUTES * 60)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)  # idle timeout slides with activity
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 3600)

    def test_production_needs_a_mail_server_for_password_reset(self):
        self.assertNotEqual(run_check({"DJANGO_EMAIL_HOST": None}).returncode, 0)
        self.assertNotEqual(run_check({"DJANGO_DEFAULT_FROM_EMAIL": None}).returncode, 0)
        self.assertEqual(run_check({"DJANGO_EMAIL_HOST": None,
                                    "DJANGO_EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}).returncode, 0)
