from rest_framework.routers import DefaultRouter

from .views import (
    AttendanceViewSet,
    EmployeeViewSet,
    LeaveApplicationViewSet,
    SalaryViewSet,
)


router = DefaultRouter()

router.register(
    "employees",
    EmployeeViewSet,
    basename="api-employees",
)

router.register(
    "attendance",
    AttendanceViewSet,
    basename="api-attendance",
)

router.register(
    "leaves",
    LeaveApplicationViewSet,
    basename="api-leaves",
)

router.register(
    "payroll",
    SalaryViewSet,
    basename="api-payroll",
)

urlpatterns = router.urls