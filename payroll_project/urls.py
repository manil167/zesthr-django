from django.contrib import admin
from django.urls import include, path
from core.admin_guide import admin_guide

urlpatterns = [
    path("admin/guide/", admin.site.admin_view(admin_guide), name="admin_guide"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("core.urls")),
    path(
    "api/v1/",
    include("core.api.urls"),
),
]
