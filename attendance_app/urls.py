from django.urls import path
from .views import (
    ApiRootView,
    AdminLoginView,
    RegisterDeviceView,
    EmployeeListCreateView,
    SyncAttendanceView,
    DeviceListView,
    AttendanceListView,
    ExportExcelReportView,
)

urlpatterns = [
    path('', ApiRootView.as_view(), name='api-root'),
    path('auth/login/', AdminLoginView.as_view(), name='admin-login'),
    path('register-device/', RegisterDeviceView.as_view(), name='register-device'),
    path('employees/', EmployeeListCreateView.as_view(), name='employees'),
    path('sync-attendance/', SyncAttendanceView.as_view(), name='sync-attendance'),
    path('devices/', DeviceListView.as_view(), name='devices'),
    path('attendance/', AttendanceListView.as_view(), name='attendance'),
    path('reports/export-excel/', ExportExcelReportView.as_view(), name='export-excel'),
]

