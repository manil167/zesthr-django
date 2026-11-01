from django.db import transaction

from ..models import Notification


@transaction.atomic
def notify(
    *,
    user,
    notification_type,
    title,
    message,
    url="",
    unique_key=None,
    organization=None,
):
    if not user:
        return None

    values = dict(organization=organization, notification_type=notification_type, title=title, message=message, url=url)
    if unique_key:
        notification, _ = Notification.objects.get_or_create(
            recipient=user, unique_key=unique_key, defaults=values)
        return notification
    return Notification.objects.create(recipient=user, unique_key="", **values)



def notify_leave_decision(leave):
    if not leave.emp.user:
        return None

    return notify(
        organization=leave.emp.organization,
        user=leave.emp.user,
        notification_type=Notification.LEAVE_DECISION,
        title="Leave application updated",
        message=(
            f"Your {leave.get_leave_type_display()} "
            f"request has been "
            f"{leave.get_status_display().lower()}."
        ),
        url="/leaves/",
        unique_key=f"leave:{leave.pk}:{leave.status}",
    )


def notify_pending_leave(leave):
    manager = leave.emp.manager

    if not manager or not manager.user:
        return None

    return notify(
        organization=leave.emp.organization,
        user=manager.user,
        notification_type=Notification.LEAVE_PENDING,
        title="Leave approval required",
        message=(
            f"{leave.emp.name} submitted "
            f"a leave request."
        ),
        url="/leaves/",
        unique_key=f"leave-pending:{leave.pk}",
    )

def notify_attendance_reviewers(organization, *, count, day):
    from django.contrib.auth.models import User
    from django.db.models import Q
    people = User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__permissions__codename="review_attendance_corrections")
        | Q(user_permissions__codename="review_attendance_corrections"))
    people = people.filter(organization_memberships__organization=organization,
                           organization_memberships__is_active=True,
                           organization_memberships__organization__is_active=True)
    for person in people.distinct():
        notify(user=person, organization=organization, notification_type=Notification.ATTENDANCE_EXCEPTION,
               title="Attendance exceptions need review",
               message=f"{count} new attendance signal(s) were identified. Review the explanations before making a decision.",
               url="/attendance/reviews/", unique_key=f"anomaly-summary:{organization.pk}:{day}")
