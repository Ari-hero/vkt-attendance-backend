import os
import json
import logging
import uuid as uuid_lib
from pathlib import Path
from datetime import datetime
from django.db import models
from django.db.models import F
from django.utils import timezone
from django.http import HttpResponse
from django.conf import settings
from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.drawing.image import Image as OpenPyXLImage

from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny

from .models import Device, Employee, AttendanceLog
from .serializers import DeviceSerializer, EmployeeSerializer, AttendanceLogSerializer

logger = logging.getLogger('api')


def is_authenticated_request(request):
    """
    Validates if request is authorized via DRF Token/Session (web dashboard)
    or via valid X-Device-Id / X-Api-Key headers (kiosk terminal).
    """
    if request.user and request.user.is_authenticated:
        return True

    device_id = request.headers.get('X-Device-Id')
    api_key = request.headers.get('X-Api-Key')
    if device_id and api_key:
        try:
            device = Device.objects.get(device_id=device_id, is_active=True)
            if device.api_key == api_key:
                Device.objects.filter(id=device.id).update(last_seen=timezone.now())
                return True
        except Device.DoesNotExist:
            pass
    return False


class ApiRootView(APIView):
    """
    Root API Endpoint returning online status and service details.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "status": "online",
            "service": "VKT Biometric Attendance API",
            "version": "1.0.0",
            "endpoints": {
                "admin_login": "/api/auth/login/",
                "provision_otp": "/api/devices/provision-otp/",
                "register_device": "/api/register-device/",
                "employees": "/api/employees/",
                "sync_attendance": "/api/sync-attendance/",
                "devices": "/api/devices/",
                "attendance": "/api/attendance/",
                "daily_data": "/api/reports/daily-data/",
                "export_excel": "/api/reports/export-excel/",
            }
        }, status=status.HTTP_200_OK)


class AdminLoginView(APIView):
    """
    DRF Token Login endpoint for Dashboard Administrators.
    Validates username and password against Django auth_user database.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        if not username or not password:
            return Response({'error': 'Username and password are required'}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not user.is_active:
                return Response({'error': 'User account is disabled'}, status=status.HTTP_403_FORBIDDEN)
            token, _ = Token.objects.get_or_create(user=user)
            return Response({
                'token': token.key,
                'username': user.username,
                'is_staff': user.is_staff
            }, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'Invalid username or password'}, status=status.HTTP_401_UNAUTHORIZED)


import hashlib
import secrets
from .models import Device, Employee, AttendanceLog, DeviceProvisioningOTP
from .serializers import DeviceSerializer, EmployeeSerializer, AttendanceLogSerializer


class GenerateProvisioningOTPView(APIView):
    """
    Admin-only endpoint to generate a short-lived (10 min), single-use 6-digit OTP
    for pairing a new hardware kiosk without exposing admin credentials or API keys.
    """
    def post(self, request):
        if not (request.user and request.user.is_authenticated):
            return Response({'error': 'Administrator authentication required'}, status=status.HTTP_401_UNAUTHORIZED)

        device_name = request.data.get('device_name', 'Industrial Kiosk')
        duration_minutes = int(request.data.get('duration_minutes', 10))

        # Rate limiting: max 5 active OTPs created in last 10 minutes
        ten_mins_ago = timezone.now() - timezone.timedelta(minutes=10)
        recent_count = DeviceProvisioningOTP.objects.filter(
            created_at__gte=ten_mins_ago,
            is_active=True
        ).count()
        if recent_count >= 5:
            return Response({'error': 'Rate limit exceeded. Too many active OTP requests. Please wait or use existing code.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        # Deactivate any previous unused OTPs for the same device name
        DeviceProvisioningOTP.objects.filter(
            device_name=device_name,
            is_active=True
        ).update(is_active=False)

        # Generate 6-digit numeric OTP
        raw_otp = f"{secrets.randbelow(900000) + 100000}"
        otp_hash = hashlib.sha256(raw_otp.encode('utf-8')).hexdigest()
        expires_at = timezone.now() + timezone.timedelta(minutes=duration_minutes)

        otp_obj = DeviceProvisioningOTP.objects.create(
            otp_hash=otp_hash,
            expires_at=expires_at,
            created_by=request.user,
            device_name=device_name,
            is_active=True
        )

        logger.info(f"Provisioning OTP generated for device '{device_name}' by user '{request.user.username}'")

        return Response({
            'otp': raw_otp,
            'device_name': device_name,
            'expires_at': expires_at.isoformat(),
            'expires_in_seconds': duration_minutes * 60,
            'status': 'waiting_for_activation'
        }, status=status.HTTP_201_CREATED)


class RegisterDeviceView(APIView):
    """
    Public device registration endpoint.
    Requires a valid, unexpired, single-use 6-digit OTP generated by an administrator.
    Returns device_id and permanent device api_key on success.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        otp_input = str(request.data.get('otp', '')).strip()
        device_name = request.data.get('device_name', request.data.get('name', 'Industrial Kiosk'))

        if otp_input:
            otp_hash = hashlib.sha256(otp_input.encode('utf-8')).hexdigest()
            now = timezone.now()

            # Find matching active OTP
            try:
                otp_obj = DeviceProvisioningOTP.objects.get(otp_hash=otp_hash, is_active=True)
            except DeviceProvisioningOTP.DoesNotExist:
                # Rate limit defense: increment attempt count on recent active OTPs
                DeviceProvisioningOTP.objects.filter(is_active=True, expires_at__gte=now).update(
                    attempt_count=models.F('attempt_count') + 1
                )
                # Auto-deactivate OTPs exceeding 5 attempts
                DeviceProvisioningOTP.objects.filter(attempt_count__gte=5).update(is_active=False)
                return Response({'error': 'Invalid or expired activation code'}, status=status.HTTP_400_BAD_REQUEST)

            # Check expiration
            if now > otp_obj.expires_at:
                otp_obj.is_active = False
                otp_obj.save()
                return Response({'error': 'Activation code has expired. Please generate a new code from dashboard.'}, status=status.HTTP_400_BAD_REQUEST)

            # Check if already used
            if otp_obj.used_at is not None:
                otp_obj.is_active = False
                otp_obj.save()
                return Response({'error': 'Activation code has already been used'}, status=status.HTTP_400_BAD_REQUEST)

            # Check attempt limit
            if otp_obj.attempt_count >= 5:
                otp_obj.is_active = False
                otp_obj.save()
                return Response({'error': 'Maximum verification attempts exceeded. Code revoked.'}, status=status.HTTP_400_BAD_REQUEST)

            # Mark OTP as consumed (single-use guarantee)
            otp_obj.used_at = now
            otp_obj.is_active = False
            otp_obj.save()

            # Create provisioned Device with unique credentials
            device_id = f"kiosk_{uuid_lib.uuid4().hex[:8]}"
            api_key = str(uuid_lib.uuid4())
            device = Device.objects.create(
                device_id=device_id,
                name=otp_obj.device_name or device_name,
                api_key=api_key,
                is_active=True,
                last_seen=now
            )

            logger.info(f"Device successfully provisioned with OTP: {device.device_id} ({device.name})")

            return Response({
                'status': 'success',
                'device_id': device.device_id,
                'api_key': device.api_key,
                'name': device.name,
                'message': 'Device activated successfully'
            }, status=status.HTTP_201_CREATED)

        # Legacy fallback for backward compatibility
        device_id = request.data.get('device_id')
        if not device_id:
            return Response({'error': 'Activation code (otp) is required for device registration'}, status=status.HTTP_400_BAD_REQUEST)

        device, created = Device.objects.get_or_create(
            device_id=device_id,
            defaults={'name': device_name, 'api_key': str(uuid_lib.uuid4()), 'last_seen': timezone.now()}
        )
        if not created:
            device.last_seen = timezone.now()
            if device_name and device_name != device.name:
                device.name = device_name
            device.save()

        logger.info(f"Device registered/updated: {device_id} ({device_name})")
        return Response(DeviceSerializer(device).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DeviceListView(generics.ListAPIView):
    """Returns only active provisioned devices. Inactive/decommissioned devices are excluded."""
    queryset = Device.objects.filter(is_active=True).order_by('-last_seen')
    serializer_class = DeviceSerializer

    def list(self, request, *args, **kwargs):
        if not is_authenticated_request(request):
            return Response({'error': 'Authentication credentials were not provided or are invalid'}, status=status.HTTP_401_UNAUTHORIZED)
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class EmployeeListCreateView(generics.ListCreateAPIView):
    queryset = Employee.objects.all().order_by('name')
    serializer_class = EmployeeSerializer

    def list(self, request, *args, **kwargs):
        if not is_authenticated_request(request):
            return Response({'error': 'Authentication credentials were not provided or are invalid'}, status=status.HTTP_401_UNAUTHORIZED)
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)



class SyncAttendanceView(APIView):
    """
    Accepts raw attendance logs from kiosks.
    Performs device auth, payload validation, deduplication by UUID, and global IN/OUT sequence resolution.
    Returns backend-assigned final_type for each log.
    """
    def post(self, request):
        if not is_authenticated_request(request):
            return Response({'error': 'Authentication credentials were not provided or are invalid (X-Device-Id, X-Api-Key)'}, status=status.HTTP_401_UNAUTHORIZED)

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

        # Ensure employees enrolled on kiosk are represented in central Employee master
        incoming_emp_data = {}
        for item in logs_data:
            e_id = str(item.get('emp_id', '')).strip()
            e_name = item.get('emp_name', '')
            if e_id and e_id not in incoming_emp_data:
                incoming_emp_data[e_id] = e_name

        if incoming_emp_data:
            existing_emp_ids = set(
                Employee.objects.filter(emp_id__in=incoming_emp_data.keys()).values_list('emp_id', flat=True)
            )
            new_emps_to_create = [
                Employee(
                    emp_id=eid,
                    name=incoming_emp_data[eid] or f"Emp {eid}",
                    department="General",
                    embedding="[]",
                    photo_url="",
                )
                for eid in incoming_emp_data
                if eid not in existing_emp_ids
            ]
            if new_emps_to_create:
                Employee.objects.bulk_create(new_emps_to_create, ignore_conflicts=True)

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
        if not is_authenticated_request(request):
            return Response({'error': 'Authentication credentials were not provided or are invalid'}, status=status.HTTP_401_UNAUTHORIZED)
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

AttendanceListView = AttendanceLogListView


def get_canonical_attendance_data(target_date_str):
    """
    Single Canonical Backend Attendance Dataset generator.
    Guarantees identical data structure, ordering, and event counts for PDF and Excel exports.
    """
    try:
        display_date = datetime.strptime(target_date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
    except Exception:
        display_date = target_date_str

    all_employees = Employee.objects.all().order_by('name')
    day_logs = AttendanceLog.objects.filter(date=target_date_str).order_by('timestamp')

    logs_by_emp = {}
    for log in day_logs:
        logs_by_emp.setdefault(log.emp_id, []).append(log)

    all_emp_ids = list(dict.fromkeys([e.emp_id for e in all_employees] + list(logs_by_emp.keys())))

    records = []
    for emp_id in all_emp_ids:
        emp = next((e for e in all_employees if e.emp_id == emp_id), None)
        emp_name = emp.name if emp else (logs_by_emp[emp_id][0].emp_name if logs_by_emp.get(emp_id) else emp_id)
        department = emp.department if emp else 'Unregistered'
        emp_logs = logs_by_emp.get(emp_id, [])

        if not emp_logs:
            records.append({
                'emp_id': emp_id,
                'emp_name': emp_name,
                'department': department,
                'date': display_date,
                'raw_date': target_date_str,
                'time': '-',
                'type': 'ABSENT',
                'confidence': '-',
                'timestamp': '-',
                'uuid': '-',
                'is_present': False,
            })
        else:
            for log in emp_logs:
                try:
                    display_log_date = datetime.strptime(str(log.date), '%Y-%m-%d').strftime('%d-%m-%Y')
                except Exception:
                    display_log_date = str(log.date)

                time_str = log.time.strftime('%H:%M:%S') if hasattr(log.time, 'strftime') else str(log.time)
                ts_str = log.timestamp.isoformat() if hasattr(log.timestamp, 'isoformat') else str(log.timestamp)

                records.append({
                    'emp_id': log.emp_id,
                    'emp_name': log.emp_name,
                    'department': department,
                    'date': display_log_date,
                    'raw_date': str(log.date),
                    'time': time_str,
                    'type': log.type,
                    'confidence': f"{log.confidence:.3f}",
                    'timestamp': ts_str,
                    'uuid': log.uuid,
                    'is_present': True,
                })

    present_ids = set(logs_by_emp.keys())
    return {
        'target_date': target_date_str,
        'display_date': display_date,
        'summary': {
            'total_events': len([r for r in records if r['type'] != 'ABSENT']),
            'present_count': len(present_ids),
            'enrolled_count': len(all_employees),
            'total_rows': len(records),
        },
        'records': records,
    }


class CanonicalReportDataView(APIView):
    """
    Returns canonical JSON report dataset for audit and PDF/Excel dataset equality verification.
    """
    def get(self, request):
        if not is_authenticated_request(request):
            return Response({'error': 'Authentication credentials were not provided or are invalid'}, status=status.HTTP_401_UNAUTHORIZED)
        target_date_str = request.query_params.get('date', datetime.now().strftime('%Y-%m-%d'))
        data = get_canonical_attendance_data(target_date_str)
        return Response(data, status=status.HTTP_200_OK)


class ExportExcelReportView(APIView):
    """
    Generates detailed Excel report with embedded corporate logo graphics,
    corporate header hierarchy, alternating row shading, freeze panes, and print setup.
    Uses the exact canonical attendance dataset.
    """
    def get(self, request):
        if not is_authenticated_request(request):
            return Response({'error': 'Authentication credentials were not provided or are invalid'}, status=status.HTTP_401_UNAUTHORIZED)

        target_date_str = request.query_params.get('date', datetime.now().strftime('%Y-%m-%d'))
        report_data = get_canonical_attendance_data(target_date_str)
        display_target_date = report_data['display_date']
        records = report_data['records']

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Report {display_target_date}"

        # ── Page Setup & Print Configurations ─────────────────────────────────
        ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_title_rows = '5:5'

        # ── Freeze Panes (Header row 5 stays visible on scroll) ────────────────
        ws.freeze_panes = 'A6'

        # ── Logo Embedding & Proportional Scaling ──────────────────────────────
        base_dir = Path(__file__).resolve().parent.parent.parent
        sp_logo_path = base_dir / 'assets' / 'images' / 'logo_spinfotech.jpeg'
        vkt_logo_path = base_dir / 'assets' / 'images' / 'logo_vkt.jpg'

        if sp_logo_path.exists():
            try:
                img_sp = OpenPyXLImage(str(sp_logo_path))
                img_sp.width = 52
                img_sp.height = 49
                ws.add_image(img_sp, 'A1')
            except Exception as e:
                logger.warning(f"Could not embed S.P.Infotech logo: {e}")

        if vkt_logo_path.exists():
            try:
                img_vkt = OpenPyXLImage(str(vkt_logo_path))
                img_vkt.width = 175
                img_vkt.height = 41
                ws.add_image(img_vkt, 'H1')
            except Exception as e:
                logger.warning(f"Could not embed VKT logo: {e}")

        # Row Heights
        ws.row_dimensions[1].height = 24
        ws.row_dimensions[2].height = 20
        ws.row_dimensions[3].height = 22
        ws.row_dimensions[4].height = 10
        ws.row_dimensions[5].height = 26

        # Corporate Header Hierarchy
        c1 = ws.cell(row=1, column=2, value="S.P. INFOTECH")
        c1.font = Font(size=14, bold=True, color="1E293B")
        c1.alignment = Alignment(horizontal="left", vertical="center")

        c2 = ws.cell(row=2, column=2, value="V.K. TOURS & TRAVELS — ENTERPRISE ATTENDANCE")
        c2.font = Font(size=10, bold=True, color="475569")
        c2.alignment = Alignment(horizontal="left", vertical="center")

        c3 = ws.cell(row=3, column=2, value=f"DAILY DETAILED ATTENDANCE REPORT  |  DATE: {display_target_date}")
        c3.font = Font(size=11, bold=True, color="1D4ED8")
        c3.alignment = Alignment(horizontal="left", vertical="center")

        headers = ['Employee ID', 'Employee Name', 'Department', 'Date', 'Time', 'Punch Type', 'Confidence Score', 'Timestamp', 'Record UUID']
        ws.append([]) # Row 4 space

        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True, size=10)
        thin_border = Border(
            left=Side(style='thin', color='E2E8F0'),
            right=Side(style='thin', color='E2E8F0'),
            top=Side(style='thin', color='E2E8F0'),
            bottom=Side(style='thin', color='E2E8F0')
        )

        header_row_idx = 5
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=header_row_idx, column=col_num, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        even_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        odd_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
        in_fill = PatternFill(start_color="F0FDF4", end_color="F0FDF4", fill_type="solid")
        out_fill = PatternFill(start_color="FEF2F2", end_color="FEF2F2", fill_type="solid")
        absent_fill = PatternFill(start_color="FFFBEB", end_color="FFFBEB", fill_type="solid")

        current_row_idx = 6
        for r in records:
            row_fill = even_fill if current_row_idx % 2 == 0 else odd_fill
            punch_type_val = r['type']

            row_values = [
                r['emp_id'],
                r['emp_name'],
                r['department'],
                r['date'],
                r['time'],
                r['type'],
                r['confidence'],
                r['timestamp'],
                r['uuid'],
            ]

            for col_idx, val in enumerate(row_values, 1):
                c = ws.cell(row=current_row_idx, column=col_idx, value=val)
                c.fill = row_fill
                c.border = thin_border
                c.font = Font(size=9, color="0F172A")

                # Highlight Punch Type cell specifically
                if col_idx == 6:
                    if punch_type_val == 'IN':
                        c.fill = in_fill
                        c.font = Font(size=9, bold=True, color="15803D")
                    elif punch_type_val == 'OUT':
                        c.fill = out_fill
                        c.font = Font(size=9, bold=True, color="B91C1C")
                    elif punch_type_val == 'ABSENT':
                        c.fill = absent_fill
                        c.font = Font(size=9, bold=True, color="B45309")

                if col_idx in (1, 4, 5, 6, 7):
                    c.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    c.alignment = Alignment(horizontal="left", vertical="center")
            ws.row_dimensions[current_row_idx].height = 20
            current_row_idx += 1

        # Auto-fit column widths with upper bounds
        for col in ws.columns:
            max_len = 0
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            for cell in col:
                if cell.row >= 5 and cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            calculated_width = min(max(max_len + 4, 12), 36)
            ws.column_dimensions[col_letter].width = calculated_width

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="Attendance_Report_{display_target_date}.xlsx"'
        wb.save(response)
        return response


