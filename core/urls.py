from . import views_compliance
from . import views_messaging, views_whatsapp
from . import views_notifications
from . import views_anomalies
from . import views_documents
from . import views_payroll
from . import views_leave
from django.contrib.auth import views as auth
from django.contrib.auth.decorators import login_required
from django.urls import path

from .views_auth import SignupView
from . import views as v
from . import views_auth as a

urlpatterns = [
    path('auth/provider/sign-up/email', a.neon_sdk_signup, name='neon_sdk_signup'),
    path('verify-email/', a.neon_verify_email, name='neon_verify_email'),
    path('help/chat/', views_messaging.assistant, name='app_assistant'),
    path('announcements/new/', views_messaging.announcement, name='announcement_create'),
    path('organization/invite/', views_messaging.invitation, name='organization_invite'),
    path('whatsapp/', views_messaging.whatsapp_link, name='whatsapp_link'),
    path('integrations/whatsapp/webhook/', views_whatsapp.webhook, name='whatsapp_webhook'),
    path('password/neon/reset/', a.neon_password_reset, name='neon_password_reset'),
    path("reports/audit/", views_compliance.report, name="compliance_report"),
    path("notifications/", views_notifications.inbox, name="notifications"),
    path("notifications/read-all/", views_notifications.mark_all_read, name="notifications_read_all"),
    path("notifications/<int:pk>/read/", views_notifications.mark_read, name="notification_read"),
    path("leaves/<int:pk>/cancel/", views_leave.cancel_pending, name="leave_cancel_pending"),
    path("attendance/reviews/", views_anomalies.queue, name="anomaly_queue"),
    path("attendance/reviews/<int:pk>/", views_anomalies.review, name="anomaly_review"),
    path("employees/<int:pk>/documents/", views_documents.document_list, name="employee_documents"),
    path("documents/<int:pk>/download/", views_documents.download, name="document_download"),
    path("documents/<int:pk>/purge/", views_documents.request_purge, name="document_request_purge"),
    path("payroll/runs/", views_payroll.runs, name="payroll_runs"),
    path("payroll/runs/<int:pk>/", views_payroll.run_detail, name="payroll_run_detail"),
    path("payroll/export.csv", views_payroll.payroll_csv, name="payroll_csv"),
    path("leaves/balances/", views_leave.balances, name="leave_balances"),
    path("employees/<int:pk>/leave-balances/", views_leave.balances, name="employee_leave_balances"),
    path("login/", a.LoginView.as_view(), name="login"),
    path("password/change/", login_required(a.PasswordChangeView.as_view()), name="password_change"),
    path("password/reset/", a.PasswordResetView.as_view(), name="password_reset"),
    path("password/reset/sent/", auth.PasswordResetDoneView.as_view(template_name="password_reset_done.html"), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", a.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/complete/", auth.PasswordResetCompleteView.as_view(template_name="password_reset_complete.html"), name="password_reset_complete"),
    path("signup/", SignupView.as_view(), name="signup"),
    path("account/pending/", a.account_pending, name="account_pending" ),
    path("logout/", a.LogoutView.as_view(), name="logout"),
    path("", v.home, name="home"),
    path("dashboard/", v.dashboard, name="dashboard"),
    path("employees/", v.EmployeeList.as_view(), name="employee_list"),
    path("employees/add/", v.EmployeeCreate.as_view(), name="employee_add"),
    path("employees/<int:pk>/edit/", v.EmployeeUpdate.as_view(), name="employee_edit"),
    path("employees/<int:pk>/delete/", v.EmployeeDelete.as_view(), name="employee_delete"),
    path("employees/<int:pk>/", v.employee_detail, name="employee_detail"),
    path("me/", v.my_portal, name="my_portal"),
    path("profile/", v.my_profile, name="my_profile"),
    path("profile/edit/", v.my_profile_edit, name="my_profile_edit"),
    path("employees/<int:pk>/deactivate/", v.employee_deactivate, name="employee_deactivate"),
    path("employees/<int:pk>/reactivate/", v.employee_reactivate, name="employee_reactivate"),
    path("employees/changes/", v.scheduled_employment_changes, name="scheduled_employment_changes"),
    path("employees/changes/<int:pk>/apply/", v.apply_scheduled_employment_change, name="apply_scheduled_employment_change"),
    path("healthz/", v.healthz, name="healthz"),
    path("attendance/register/", v.attendance_register, name="attendance_register"),
    path("attendance/bulk/", v.attendance_bulk, name="attendance_bulk"),
    path("attendance/history/", v.attendance_history, name="attendance_history"),
    path("attendance/exceptions/", v.attendance_exceptions, name="attendance_exceptions"),
    path("attendance/missing-punches/", v.attendance_missing_punches, name="attendance_missing_punches"),
    path("attendance/corrections/", v.correction_list, name="correction_list"),
    path("attendance/corrections/mine/", v.my_corrections, name="my_corrections"),
    path("attendance/corrections/new/", v.correction_new, name="correction_new"),
    path("attendance/corrections/<int:pk>/cancel/", v.correction_cancel, name="correction_cancel"),
    path("attendance/corrections/<int:pk>/decide/", v.correction_decide, name="correction_decide"),
    path("attendance/today/", v.attendance_today, name="attendance_today"),
    path("attendance/", v.AttendanceList.as_view(), name="attendance_list"),
    path("attendance/add/", v.AttendanceCreate.as_view(), name="attendance_add"),
    path("attendance/<int:pk>/edit/", v.AttendanceUpdate.as_view(), name="attendance_edit"),
    path("attendance/<int:pk>/delete/", v.AttendanceDelete.as_view(), name="attendance_delete"),
    path("reports/monthly/", v.monthly_report, name="report"),
    path("payroll/", v.payroll, name="payroll"),
    path("payroll/slip/<int:pk>/", v.slip, name="slip"),
    path("holidays/", v.holidays_list, name="holidays"),
    path("leaves/", v.leaves_list, name="leaves_list"),
    path("leaves/apply/", v.leave_apply, name="leave_apply"),
    path("leaves/<int:pk>/action/", v.leave_action, name="leave_action"),
    path(
    "reports/attendance.csv",
    v.attendance_report_csv,
    name="attendance_report_csv",
),
]
