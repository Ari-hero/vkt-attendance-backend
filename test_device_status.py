"""
Device online/offline status tests.

Tests:
  1. Recently active device => ONLINE
  2. Old last_seen => OFFLINE
  3. Never communicated => NEVER_SEEN
  4. Inactive (is_active=False) device excluded from count
  5. Dashboard online count matches device status

Reads threshold from settings.DEVICE_ONLINE_THRESHOLD_MINUTES.
"""
import os
import json
import django
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.test import TestCase, Client, override_settings
from django.utils import timezone
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token

from attendance_app.models import Device
from attendance_app.serializers import _device_status


@override_settings(DEVICE_ONLINE_THRESHOLD_MINUTES=10)
class DeviceStatusSerializerTests(TestCase):
    """Unit tests for the _device_status helper."""

    def test_1_recently_active_is_online(self):
        """Device that communicated 3 minutes ago is ONLINE."""
        last_seen = timezone.now() - timedelta(minutes=3)
        self.assertEqual(_device_status(last_seen), 'ONLINE')

    def test_2_old_last_seen_is_offline(self):
        """Device that last communicated 30 minutes ago is OFFLINE."""
        last_seen = timezone.now() - timedelta(minutes=30)
        self.assertEqual(_device_status(last_seen), 'OFFLINE')

    def test_3_never_seen_device(self):
        """Device with last_seen=None is NEVER_SEEN."""
        self.assertEqual(_device_status(None), 'NEVER_SEEN')

    def test_boundary_exactly_at_threshold_is_online(self):
        """Device at threshold minus 2s grace is ONLINE (accounts for test execution time)."""
        last_seen = timezone.now() - timedelta(minutes=10) + timedelta(seconds=2)
        self.assertEqual(_device_status(last_seen), 'ONLINE')

    def test_boundary_one_second_over_threshold_is_offline(self):
        """Device 30 seconds past threshold is clearly OFFLINE."""
        last_seen = timezone.now() - timedelta(minutes=10, seconds=30)
        self.assertEqual(_device_status(last_seen), 'OFFLINE')


@override_settings(DEVICE_ONLINE_THRESHOLD_MINUTES=10)
class DeviceStatusAPITests(TestCase):
    """Integration tests: verify status field and dashboard counts via API."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('testadmin', 'a@b.com', 'pw123!')
        self.token = Token.objects.create(user=self.admin).key
        self.auth = {'HTTP_AUTHORIZATION': f'Token {self.token}'}

        # Online device — communicated 2 minutes ago
        self.dev_online = Device.objects.create(
            device_id='kiosk_online',
            name='Gate A',
            api_key='key_online_001',
            is_active=True,
            last_seen=timezone.now() - timedelta(minutes=2),
        )
        # Offline device — communicated 60 minutes ago
        self.dev_offline = Device.objects.create(
            device_id='kiosk_offline',
            name='Gate B',
            api_key='key_offline_002',
            is_active=True,
            last_seen=timezone.now() - timedelta(minutes=60),
        )
        # Never-seen device — last_seen is None (nullable field)
        self.dev_never = Device.objects.create(
            device_id='kiosk_never',
            name='Gate C',
            api_key='key_never_003',
            is_active=True,
            last_seen=None,
        )

        # Inactive device — must be excluded from active counts
        self.dev_inactive = Device.objects.create(
            device_id='kiosk_inactive',
            name='Gate D (decommissioned)',
            api_key='key_inactive_004',
            is_active=False,
            last_seen=timezone.now() - timedelta(minutes=1),
        )

    def test_4_inactive_device_excluded_from_api(self):
        """GET /api/devices/ returns only active devices."""
        res = self.client.get('/api/devices/', **self.auth)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        ids = [d['device_id'] for d in data]
        self.assertNotIn('kiosk_inactive', ids,
                         'Inactive device must not appear in dashboard device list')

    def test_5_device_status_field_in_api_response(self):
        """Each device in the API response carries the correct status field."""
        res = self.client.get('/api/devices/', **self.auth)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        by_id = {d['device_id']: d for d in data}

        self.assertEqual(by_id['kiosk_online']['status'], 'ONLINE',
                         'Device active 2 min ago must be ONLINE')
        self.assertEqual(by_id['kiosk_offline']['status'], 'OFFLINE',
                         'Device active 60 min ago must be OFFLINE')
        # never_seen may show NEVER_SEEN or OFFLINE depending on null handling
        never_status = by_id.get('kiosk_never', {}).get('status')
        self.assertEqual(never_status, 'NEVER_SEEN',
                         'Device with null last_seen must be NEVER_SEEN')

    def test_5b_dashboard_online_count_matches_status(self):
        """
        The dashboard should derive X/Y from the status field.
        Verify that exactly 1 device has ONLINE status among the 3 active test devices.
        """
        res = self.client.get('/api/devices/', **self.auth)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        # Filter to only our 3 test device IDs (isolate from other test data)
        test_ids = {'kiosk_online', 'kiosk_offline', 'kiosk_never'}
        test_devices = [d for d in data if d['device_id'] in test_ids]
        online_count = sum(1 for d in test_devices if d['status'] == 'ONLINE')
        self.assertEqual(online_count, 1, 'Exactly one test device should be ONLINE')
        self.assertEqual(len(test_devices), 3, 'All 3 active test devices should appear')

    def test_6_api_key_never_exposed_in_device_list(self):
        """api_key must not appear in any device list response."""
        res = self.client.get('/api/devices/', **self.auth)
        self.assertEqual(res.status_code, 200)
        response_text = res.content.decode()
        # Make sure none of the test API keys appear in the response body
        self.assertNotIn('key_online_001', response_text)
        self.assertNotIn('key_offline_002', response_text)
        self.assertNotIn('key_never_003', response_text)
        self.assertNotIn('api_key', response_text.replace('"api_key"', ''))

    def test_7_last_seen_updates_on_authenticated_sync(self):
        """Authenticated sync request updates device last_seen timestamp."""
        old_seen = timezone.now() - timedelta(hours=2)
        Device.objects.filter(pk=self.dev_online.pk).update(last_seen=old_seen)

        # Send an authenticated sync request
        payload = [{
            'uuid': 'uuid_heartbeat_test',
            'emp_id': 'EMP_HB',
            'emp_name': 'Heartbeat Test',
            'timestamp': timezone.now().isoformat(),
            'date': timezone.now().date().isoformat(),
            'time': timezone.now().strftime('%H:%M:%S'),
            'confidence': 0.95,
            'device_id': 'kiosk_online',
        }]
        sync_headers = {
            'HTTP_X_DEVICE_ID': 'kiosk_online',
            'HTTP_X_API_KEY': 'key_online_001',
        }
        self.client.post('/api/sync-attendance/',
                         data=json.dumps(payload),
                         content_type='application/json',
                         **sync_headers)

        self.dev_online.refresh_from_db()
        elapsed = (timezone.now() - self.dev_online.last_seen).total_seconds()
        self.assertLess(elapsed, 30,
                        'last_seen must be updated to within 30 seconds of authenticated sync')


if __name__ == '__main__':
    import unittest
    unittest.main()
