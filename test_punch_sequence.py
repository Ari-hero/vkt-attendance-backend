import os
import django
from django.test import TestCase, Client
from django.utils import timezone
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from attendance_app.models import AttendanceLog, Device

class PunchSequenceTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.device = Device.objects.create(
            device_id='kiosk_site_1',
            name='Test Kiosk',
            api_key='test_key_123',
            is_active=True
        )
        self.device_headers = {
            'HTTP_X_DEVICE_ID': 'kiosk_site_1',
            'HTTP_X_API_KEY': 'test_key_123',
        }

    def test_punch_sequence_same_day(self):
        emp_id = "EMP_TEST_101"
        date_str = "2026-08-15"
        base_dt = datetime(2026, 8, 15, 9, 0, 0)

        # 5 punches sequence
        expected_types = ['IN', 'OUT', 'IN', 'OUT', 'IN']
        for i, expected in enumerate(expected_types):
            dt = base_dt + timedelta(minutes=i*10)
            payload = [{
                'uuid': f'uuid_seq_{i}',
                'emp_id': emp_id,
                'emp_name': 'Test User',
                'timestamp': dt.isoformat(),
                'date': date_str,
                'time': dt.strftime('%H:%M:%S'),
                'confidence': 0.95,
                'device_id': 'kiosk_site_1'
            }]
            resp = self.client.post('/api/sync-attendance/', payload, content_type='application/json', **self.device_headers)
            self.assertEqual(resp.status_code, 200)
            res_data = resp.json()
            self.assertEqual(res_data['processed'][0]['final_type'], expected)

        logs = list(AttendanceLog.objects.filter(emp_id=emp_id).order_by('timestamp'))
        actual_types = [l.type for l in logs]
        self.assertEqual(actual_types, expected_types)

if __name__ == '__main__':
    import unittest
    unittest.main()
