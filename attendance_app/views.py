import json
import logging
import uuid as uuid_lib
from datetime import datetime
from django.utils import timezone
from django.http import HttpResponse
from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from .models import Device, Employee, AttendanceLog
from .serializers import DeviceSerializer, EmployeeSerializer, AttendanceLogSerializer

logger = logging.getLogger('api')


class ApiRootView(APIView):
    """
    Root API Endpoint returning online status and service details.
    """
    def get(self, request):
        return Response({
            "status": "online",
            "service": "VKT Biometric Attendance API",
            "version": "1.0.0",
            "endpoints": {
                "register_device": "/api/register-device/",
                "employees": "/api/employees/",
                "sync_attendance": "/api/sync-attendance/",
                "devices": "/api/devices/",
                "attendance": "/api/attendance/",
                "export_excel": "/api/reports/export-excel/",
            }
        }, status=status.HTTP_200_OK)


class RegisterDeviceView(APIView):
    def post(self, request):
        device_id = request.data.get('device_id')
        name = request.data.get('device_name', request.data.get('name', 'Industrial Kiosk'))
        if not device_id:
            device_id = f"kiosk_{uuid_lib.uuid4().hex[:8]}"

        device, created = Device.objects.get_or_create(
            device_id=device_id,
            defaults={'name': name, 'api_key': str(uuid_lib.uuid4()), 'last_seen': timezone.now()}
        )
        if not created:
            device.last_seen = timezone.now()
            if name and name != device.name:
                device.name = name
            device.save()

        logger.info(f"Device registered/updated: {device_id} ({name})")
        return Response(DeviceSerializer(device).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DeviceListView(generics.ListAPIView):
    queryset = Device.objects.all().order_by('-last_seen')
    serializer_class = DeviceSerializer

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class EmployeeListCreateView(generics.ListCreateAPIView):
    queryset = Employee.objects.all().order_by('name')
    serializer_class = EmployeeSerializer

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SyncAttendanceView(APIView):
    def post(self, request):
        device_id = request.headers.get('X-Device-Id')
        if device_id:
            Device.objects.filter(device_id=device_id).update(last_seen=timezone.now())

        logs_data = request.data
        if not isinstance(logs_data, list):
            logs_data = [logs_data]

        if len(logs_data) > 500:
            return Response({'error': 'Batch size exceeds 500 logs limit'}, status=status.HTTP_400_BAD_REQUEST)

        processed_list = []
        incoming_uuids = [item.get('uuid') for item in logs_data if item.get('uuid')]

        existing_logs = {
            log.uuid: log for log in AttendanceLog.objects.filter(uuid__in=incoming_uuids)
        }

        new_logs_to_create = []

        for item in logs_data:
            uuid_str = item.get('uuid')
            emp_id = item.get('emp_id')
            emp_name = item.get('emp_name')
            timestamp_str = item.get('timestamp')
            date_str = item.get('date')
            time_str = item.get('time')
            confidence = item.get('confidence', 1.0)
            dev_id = item.get('device_id', device_id or 'default_kiosk')

            if not uuid_str or not emp_id:
                continue

            if uuid_str in existing_logs:
                existing = existing_logs[uuid_str]
                processed_list.append({
                    'uuid': uuid_str,
                    'emp_id': emp_id,
                    'final_type': existing.type,
                    'server_timestamp': existing.synced_at.isoformat(),
                })
                continue

            try:
                dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except Exception:
                dt = timezone.now()

            # Global IN/OUT Sequence Resolution across ALL devices for this employee
            last_log = AttendanceLog.objects.filter(emp_id=emp_id).order_by('-timestamp').first()
            assigned_type = 'OUT' if (last_log and last_log.type == 'IN') else 'IN'

            new_log = AttendanceLog(
                uuid=uuid_str,
                emp_id=emp_id,
                emp_name=emp_name or f"Emp {emp_id}",
                timestamp=dt,
                date=date_str or dt.strftime('%Y-%m-%d'),
                time=time_str or dt.strftime('%H:%M:%S'),
                type=assigned_type,
                confidence=confidence,
                device_id=dev_id,
            )
            new_logs_to_create.append(new_log)
            processed_list.append({
                'uuid': uuid_str,
                'emp_id': emp_id,
                'final_type': assigned_type,
                'server_timestamp': timezone.now().isoformat(),
            })

        if new_logs_to_create:
            AttendanceLog.objects.bulk_create(new_logs_to_create, ignore_conflicts=True)
            logger.info(f"Synced {len(new_logs_to_create)} new logs.")

        return Response({
            'status': 'success',
            'synced_count': len(processed_list),
            'processed': processed_list,
        }, status=status.HTTP_200_OK)


class AttendanceLogListView(generics.ListAPIView):
    serializer_class = AttendanceLogSerializer

    def get_queryset(self):
        queryset = AttendanceLog.objects.all().order_by('-timestamp')
        date_param = self.request.query_params.get('date')
        emp_id = self.request.query_params.get('emp_id')
        log_type = self.request.query_params.get('type')

        if date_param:
            queryset = queryset.filter(date=date_param)
        if emp_id:
            queryset = queryset.filter(emp_id=emp_id)
        if log_type and log_type != 'ALL':
            queryset = queryset.filter(type=log_type)

        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

AttendanceListView = AttendanceLogListView


class ExportExcelReportView(APIView):
    def get(self, request):
        target_date_str = request.query_params.get('date', datetime.now().strftime('%Y-%m-%d'))
        
        all_employees = Employee.objects.all().order_by('name')
        day_logs = AttendanceLog.objects.filter(date=target_date_str).order_by('timestamp')

        logs_by_emp = {}
        for log in day_logs:
            logs_by_emp.setdefault(log.emp_id, []).append(log)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Report {target_date_str}"

        headers = ['Employee ID', 'Employee Name', 'Department', 'Date', 'First IN Time', 'Last OUT Time', 'Total Punch Count', 'Status']
        ws.append(headers)

        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        for emp in all_employees:
            emp_logs = logs_by_emp.get(emp.emp_id, [])
            if emp_logs:
                first_in_log = next((l for l in emp_logs if l.type == 'IN'), emp_logs[0])
                last_out_log = next((l for l in reversed(emp_logs) if l.type == 'OUT'), emp_logs[-1])
                
                first_in = str(first_in_log.time)
                last_out = str(last_out_log.time)
                punch_count = len(emp_logs)
                status_str = 'PRESENT'
            else:
                first_in = '-'
                last_out = '-'
                punch_count = 0
                status_str = 'ABSENT'

            ws.append([emp.emp_id, emp.name, emp.department, target_date_str, first_in, last_out, punch_count, status_str])

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="Attendance_Report_{target_date_str}.xlsx"'
        wb.save(response)
        return response
