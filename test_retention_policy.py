import os
import django
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.test import TestCase
from django.utils import timezone
from django.core.management import call_command
from attendance_app.models import AttendanceLog, Employee, Device

class RetentionPolicyTestCase(TestCase):
    def setUp(self):
        self.emp = Employee.objects.create(
            emp_id="EMP_RETENTION_001",
            name="Retention Test User",
            department="QA"
        )
        self.device = Device.objects.create(
            device_id="kiosk_test_retention",
            name="Test Kiosk"
        )

        now = timezone.now()
        today = now.date()

        # Create sample logs across retention threshold
        # 1. Recent (Today) - 0 days old -> KEEP
        AttendanceLog.objects.create(
            uuid="log_0_days",
            emp_id=self.emp.emp_id,
            emp_name=self.emp.name,
            timestamp=now,
            date=today.strftime('%Y-%m-%d'),
            time=now.time(),
            type="IN",
            confidence=0.98
        )

        # 2. 44 days old -> KEEP
        dt_44 = now - timedelta(days=44)
        AttendanceLog.objects.create(
            uuid="log_44_days",
            emp_id=self.emp.emp_id,
            emp_name=self.emp.name,
            timestamp=dt_44,
            date=dt_44.strftime('%Y-%m-%d'),
            time=dt_44.time(),
            type="OUT",
            confidence=0.98
        )

        # 3. 45 days old -> KEEP (boundary test: exactly 45 days old is on cutoff date)
        dt_45 = now - timedelta(days=45)
        AttendanceLog.objects.create(
            uuid="log_45_days",
            emp_id=self.emp.emp_id,
            emp_name=self.emp.name,
            timestamp=dt_45,
            date=dt_45.strftime('%Y-%m-%d'),
            time=dt_45.time(),
            type="IN",
            confidence=0.98
        )

        # 4. 46 days old -> DELETE (> 45 days old)
        dt_46 = now - timedelta(days=46)
        AttendanceLog.objects.create(
            uuid="log_46_days",
            emp_id=self.emp.emp_id,
            emp_name=self.emp.name,
            timestamp=dt_46,
            date=dt_46.strftime('%Y-%m-%d'),
            time=dt_46.time(),
            type="OUT",
            confidence=0.98
        )

        # 5. 60 days old -> DELETE (> 45 days old)
        dt_60 = now - timedelta(days=60)
        AttendanceLog.objects.create(
            uuid="log_60_days",
            emp_id=self.emp.emp_id,
            emp_name=self.emp.name,
            timestamp=dt_60,
            date=dt_60.strftime('%Y-%m-%d'),
            time=dt_60.time(),
            type="IN",
            confidence=0.98
        )

    def test_dry_run_retention(self):
        initial_count = AttendanceLog.objects.count()
        self.assertEqual(initial_count, 5)

        # Run dry run
        call_command('purge_old_attendance', dry_run=True)

        # Confirm no records were deleted
        self.assertEqual(AttendanceLog.objects.count(), 5)

    def test_purge_execution(self):
        self.assertEqual(AttendanceLog.objects.count(), 5)

        # Execute purge
        call_command('purge_old_attendance')

        # Remaining records should be 3 (0-day, 44-day, 45-day)
        remaining_uuids = set(AttendanceLog.objects.values_list('uuid', flat=True))
        self.assertIn("log_0_days", remaining_uuids)
        self.assertIn("log_44_days", remaining_uuids)
        self.assertIn("log_45_days", remaining_uuids)
        self.assertNotIn("log_46_days", remaining_uuids)
        self.assertNotIn("log_60_days", remaining_uuids)

        # Confirm Employees and Devices are untouched
        self.assertTrue(Employee.objects.filter(emp_id=self.emp.emp_id).exists())
        self.assertTrue(Device.objects.filter(device_id=self.device.device_id).exists())

if __name__ == '__main__':
    import unittest
    unittest.main()
