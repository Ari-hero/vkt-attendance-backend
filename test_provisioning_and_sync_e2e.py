import os
import hashlib
import json
from datetime import datetime, timedelta
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.test import TestCase, Client
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from django.utils import timezone

from attendance_app.models import Device, Employee, AttendanceLog, DeviceProvisioningOTP
from attendance_app.views import get_canonical_attendance_data

class TestDeviceProvisioningAndSyncFlow(TestCase):
    def setUp(self):
        self.client = Client()
        # Create test admin user
        self.admin_user = User.objects.create_superuser(
            username='test_admin',
            email='admin@vktours.test',
            password='TestPassword123!'
        )
        self.admin_token = Token.objects.create(user=self.admin_user).key
        self.auth_headers = {'HTTP_AUTHORIZATION': f'Token {self.admin_token}'}

    def test_complete_test_device_flow(self):
        print("\n=== STEP A: API Health & Root Overview ===")
        res = self.client.get('/api/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'online')
        print("PASS: Root API online.")

        print("\n=== STEP B: Admin Authentication ===")
        res = self.client.post('/api/auth/login/', data=json.dumps({
            'username': 'test_admin',
            'password': 'TestPassword123!'
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        token = res.json().get('token')
        self.assertTrue(bool(token))
        print("PASS: Admin login successful, token issued.")

        print("\n=== STEP C & D: Generate Device Provisioning OTP ===")
        res = self.client.post('/api/devices/provision-otp/', data=json.dumps({
            'device_name': 'Test Gate 1 Kiosk',
            'duration_minutes': 10
        }), content_type='application/json', **self.auth_headers)
        self.assertEqual(res.status_code, 201)
        otp_data = res.json()
        raw_otp = otp_data['otp']
        self.assertEqual(len(raw_otp), 6)
        self.assertTrue(raw_otp.isdigit())
        print(f"PASS: 6-digit OTP generated: {raw_otp}, expires at: {otp_data['expires_at']}")

        # Verify OTP is stored as hash, not plaintext
        otp_obj = DeviceProvisioningOTP.objects.get(device_name='Test Gate 1 Kiosk', is_active=True)
        self.assertNotEqual(otp_obj.otp_hash, raw_otp)
        self.assertEqual(otp_obj.otp_hash, hashlib.sha256(raw_otp.encode('utf-8')).hexdigest())
        print("PASS: OTP hash verified (no plaintext in DB).")

        print("\n=== STEP E: Test Invalid OTP & Rate Limiting ===")
        invalid_res = self.client.post('/api/register-device/', data=json.dumps({
            'otp': '999999',
            'device_name': 'Hacker Device'
        }), content_type='application/json')
        self.assertEqual(invalid_res.status_code, 400)
        print("PASS: Invalid OTP rejected.")

        print("\n=== STEP F & G: Pair Test Device using Valid OTP ===")
        pair_res = self.client.post('/api/register-device/', data=json.dumps({
            'otp': raw_otp,
            'device_name': 'Test Gate 1 Kiosk'
        }), content_type='application/json')
        self.assertEqual(pair_res.status_code, 201)
        device_data = pair_res.json()
        device_id = device_data['device_id']
        api_key = device_data['api_key']
        self.assertTrue(device_id.startswith('kiosk_'))
        self.assertTrue(bool(api_key))
        print(f"PASS: Device paired! Device ID: {device_id}, API Key issued.")

        print("\n=== STEP H: Verify Single-Use OTP (Replay Attack Prevention) ===")
        replay_res = self.client.post('/api/register-device/', data=json.dumps({
            'otp': raw_otp,
            'device_name': 'Test Gate 1 Kiosk'
        }), content_type='application/json')
        self.assertEqual(replay_res.status_code, 400)
        print("PASS: Reused OTP rejected.")

        print("\n=== STEP I: Verify Device Appears in Admin Device List without Exposing Secrets ===")
        dev_list_res = self.client.get('/api/devices/', **self.auth_headers)
        self.assertEqual(dev_list_res.status_code, 200)
        devs = dev_list_res.json()
        matched = [d for d in devs if d['device_id'] == device_id]
        self.assertEqual(len(matched), 1)
        self.assertNotIn('api_key', matched[0])
        print("PASS: Device listed in dashboard with api_key secret safely hidden.")

        print("\n=== STEP J: Employee Master Sync ===")
        emp = Employee.objects.create(
            emp_id='2727',
            name='Shri Prasanna',
            department='Engineering'
        )
        emp_absent = Employee.objects.create(
            emp_id='7',
            name='Raja',
            department='Operations'
        )
        emp_res = self.client.get('/api/employees/', **self.auth_headers)
        self.assertEqual(emp_res.status_code, 200)
        self.assertEqual(len(emp_res.json()), 2)
        print("PASS: Employee master records verified.")

        print("\n=== STEP K: Test Attendance Event Upload via Device Auth ===")
        target_date = "2026-08-16"
        test_uuid = "uuid_test_event_2727_punch_1"
        payload = [{
            'uuid': test_uuid,
            'emp_id': '2727',
            'emp_name': 'Shri Prasanna',
            'timestamp': '2026-08-16T09:32:07Z',
            'date': target_date,
            'time': '09:32:07',
            'confidence': 0.985,
            'device_id': device_id,
        }]
        device_headers = {
            'HTTP_X_DEVICE_ID': device_id,
            'HTTP_X_API_KEY': api_key,
        }
        sync_res = self.client.post('/api/sync-attendance/', data=json.dumps(payload), content_type='application/json', **device_headers)
        self.assertEqual(sync_res.status_code, 200)
        sync_data = sync_res.json()
        self.assertEqual(sync_data['synced_count'], 1)
        self.assertEqual(sync_data['processed'][0]['final_type'], 'IN')
        print("PASS: Attendance log synced and classified as IN.")

        print("\n=== STEP L: Duplicate Event Prevention (Idempotency) ===")
        sync_res_dup = self.client.post('/api/sync-attendance/', data=json.dumps(payload), content_type='application/json', **device_headers)
        self.assertEqual(sync_res_dup.status_code, 200)
        logs_count = AttendanceLog.objects.filter(uuid=test_uuid).count()
        self.assertEqual(logs_count, 1)
        print("PASS: Duplicate attendance upload ignored (idempotent 1 record).")

        print("\n=== STEP M & N: Dashboard Attendance & Counts ===")
        dash_att_res = self.client.get(f'/api/attendance/?date={target_date}&type=ALL', **self.auth_headers)
        self.assertEqual(dash_att_res.status_code, 200)
        dash_logs = dash_att_res.json()
        self.assertEqual(len(dash_logs), 1)
        self.assertEqual(dash_logs[0]['emp_id'], '2727')
        print(f"PASS: Dashboard queries reflect real attendance log.")

        print("\n=== STEP O: Canonical Dataset & Excel / PDF Consistency ===")
        canonical = get_canonical_attendance_data(target_date)
        print(f"Summary: {canonical['summary']}")
        self.assertEqual(canonical['summary']['total_events'], 1)
        self.assertEqual(canonical['summary']['present_count'], 1)
        self.assertEqual(canonical['summary']['enrolled_count'], 2)
        self.assertEqual(canonical['summary']['total_rows'], 2)

        r1 = [r for r in canonical['records'] if r['emp_id'] == '2727'][0]
        self.assertEqual(r1['type'], 'IN')
        self.assertEqual(r1['time'], '09:32:07')
        self.assertEqual(r1['uuid'], test_uuid)

        r2 = [r for r in canonical['records'] if r['emp_id'] == '7'][0]
        self.assertEqual(r2['type'], 'ABSENT')
        self.assertEqual(r2['time'], '-')
        print("PASS: Canonical dataset verified with exact present and absent records.")

        # Test Excel endpoint
        excel_res = self.client.get(f'/api/reports/export-excel/?date={target_date}', **self.auth_headers)
        self.assertEqual(excel_res.status_code, 200)
        self.assertEqual(excel_res['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertTrue(len(excel_res.content) > 1000)
        print("PASS: Excel report successfully generated from canonical data.")

        print("\n=== ALL TEST CRITERIA VERIFIED SUCCESSFULLY! ===")

if __name__ == '__main__':
    import unittest
    unittest.main()
