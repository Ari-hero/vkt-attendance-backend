from django.urls import path
from .views import (
    RegisterDeviceView,
    DeviceListView,
    EmployeeListCreateView,
    SyncAttendanceView,
    AttendanceLogListView,
    ExportExcelReportView,
)

urlpatterns = [
    path('register-device/', RegisterDeviceView.as_view(), name='register-device'),
    path('devices/', DeviceListView.as_view(), name='device-list'),
    path('employees/', EmployeeListCreateView.as_view(), name='employees'),
    path('sync-attendance/', SyncAttendanceView.as_view(), name='sync-attendance'),
    path('attendance/', AttendanceLogListView.as_view(), name='attendance-list'),
    path('reports/export-excel/', ExportExcelReportView.as_view(), name='export-excel'),
]
