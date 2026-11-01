from datetime import date
from django.urls import reverse
from core.models import Employee, LeaveApplication, Notification
from core.services import leave, notifications
from .test_permissions import BaseTest


class NotificationTests(BaseTest):
    def setUp(self):
        self.item = notifications.notify(user=self.alice_user, notification_type=Notification.LEAVE_DECISION,
            title="Your update", message="Private message", url="/leaves/", unique_key="leave-update")

    def test_inbox_and_read_actions_are_owned_by_recipient(self):
        self.login(self.bob_user)
        self.assertNotContains(self.client.get(reverse("notifications")), "Private message")
        self.assertEqual(self.client.post(reverse("notification_read", args=[self.item.pk])).status_code, 404)
        self.login(self.alice_user)
        self.assertContains(self.client.get(reverse("notifications")), "Private message")
        self.assertEqual(self.client.get(reverse("notification_read", args=[self.item.pk])).status_code, 405)
        self.client.post(reverse("notification_read", args=[self.item.pk]))
        self.item.refresh_from_db()
        self.assertTrue(self.item.read)

    def test_mark_all_read_does_not_affect_other_recipients(self):
        self.login(self.bob_user)
        self.client.post(reverse("notifications_read_all"))
        self.item.refresh_from_db()
        self.assertFalse(self.item.read)

    def test_notification_retries_are_idempotent(self):
        notifications.notify(user=self.alice_user, notification_type=Notification.LEAVE_DECISION,
                             title="Duplicate", message="Duplicate", unique_key="leave-update")
        self.assertEqual(Notification.objects.count(), 1)

    def test_unsafe_notification_link_is_not_rendered(self):
        Notification.objects.filter(pk=self.item.pk).update(url="javascript:alert(1)")
        self.login(self.alice_user)
        self.assertNotContains(self.client.get(reverse("notifications")), "javascript:")


class ManagerLeaveQueueTests(BaseTest):
    def setUp(self):
        self.manager_profile = Employee.objects.create(name="Manager", basic_salary=1000,
                                                       user=self.manager, joined_on=date(2025, 1, 1))
        self.alice.manager = self.manager_profile
        self.alice.save()
        self.request = leave.submit_leave(self.alice, leave_type="CL", start_date=date(2026, 3, 2),
                                           end_date=date(2026, 3, 2), reason="Family")

    def test_manager_is_notified_and_queue_contains_only_direct_reports(self):
        self.assertTrue(Notification.objects.filter(recipient=self.manager, unique_key=f"leave-pending:{self.request.pk}").exists())
        self.login(self.manager)
        response = self.client.get(reverse("leaves_list"))
        self.assertContains(response, "Alice")
        self.assertNotContains(response, "Bob")
        self.client.post(reverse("leave_action", args=[self.request.pk]), {"action": "approve"})
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "APPROVED")
        self.assertEqual(self.client.post(reverse("leave_action", args=[self.leave.pk]),
                                         {"action": "approve"}).status_code, 404)

    def test_employee_can_cancel_pending_leave_with_explanation(self):
        self.login(self.alice_user)
        url = reverse("leave_cancel_pending", args=[self.request.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {"reason": "Plans changed"})
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "CANCELLED")
