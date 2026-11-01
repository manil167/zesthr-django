from core.permissions import get_employee_profile
from .models import AttendanceCorrectionRequest, Employee
from .services import corrections
from .services.messaging import visible_notifications


def review_flags(request):
    """Navigation hints: does this user review attendance corrections, and how many are waiting?

    Costs at most two small queries, and only for signed-in users who have an employee profile
    or the HR review permission.
    """
    user = request.user
    if not user.is_authenticated:
        return {"pending_corrections": None}
    profile = get_employee_profile(user)
    is_hr = user.has_perm("core.review_attendance_corrections")
    if profile is None and not is_hr:
        return {"pending_corrections": None}
    is_manager = profile is not None and Employee.objects.filter(manager=profile, is_active=True).exists()
    if not (is_hr or is_manager):
        return {"pending_corrections": None}
    from django.contrib.auth import get_user_model
    from django.db.models import Count, Subquery
    from django.db.models.functions import Coalesce
    from .models import Notification
    waiting = corrections.reviewable_requests(user).filter(
        status=AttendanceCorrectionRequest.PENDING).order_by().values("status").annotate(
            total=Count("pk")).values("total")
    unread = visible_notifications(user).filter(read=False).order_by().values(
        "recipient_id").annotate(total=Count("pk")).values("total")
    counts = get_user_model().objects.filter(pk=user.pk).annotate(
        pending_count=Coalesce(Subquery(waiting[:1]), 0),
        unread_count=Coalesce(Subquery(unread[:1]), 0)).values("pending_count", "unread_count").get()
    request._hros_unread_notifications = counts["unread_count"]
    return {"is_correction_reviewer": True, "pending_corrections": counts["pending_count"]}


def notification_flags(request):
    from .models import Notification
    if not request.user.is_authenticated:
        return {}
    count = getattr(request, "_hros_unread_notifications", None)
    if count is None:
        count = visible_notifications(request.user).filter(read=False).count()
    return {"unread_notifications": count}
