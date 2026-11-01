from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .models import Notification
from .services.messaging import visible_notifications


@login_required
def inbox(request):
    items = visible_notifications(request.user)
    unread = request.GET.get("unread") == "1"
    if unread:
        items = items.filter(read=False)
    page = Paginator(items, 30).get_page(request.GET.get("page"))
    for item in page:
        item.safe_url = item.url if item.url.startswith("/") and not item.url.startswith("//") else ""
    return render(request, "notifications.html", {"items": page, "unread": unread})


@login_required
@require_POST
def mark_read(request, pk):
    item = get_object_or_404(visible_notifications(request.user), pk=pk)
    visible_notifications(request.user).filter(pk=item.pk).update(read=True)
    return redirect("notifications")


@login_required
@require_POST
def mark_all_read(request):
    visible_notifications(request.user).filter(read=False).update(read=True)
    return redirect("notifications")
