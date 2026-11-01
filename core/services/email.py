from django.core.mail import send_mail
from django.conf import settings


def send_notification_email(
    *,
    recipient,
    subject,
    message,
):
    if not recipient:
        return 0

    return send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=True,
    )