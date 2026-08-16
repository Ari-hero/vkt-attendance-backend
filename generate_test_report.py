import os
import zipfile
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.test import RequestFactory
from attendance_app.views import ExportExcelReportView

factory = RequestFactory()
request = factory.get('/attendance/reports/export-excel/?date=2026-08-15')

import attendance_app.views
attendance_app.views.is_authenticated_request = lambda req: True

view = ExportExcelReportView.as_view()
response = view(request)

out_file = 'test_attendance_report_v2.xlsx'
with open(out_file, 'wb') as f:
    f.write(response.content)

print(f"Successfully generated {out_file}, size: {len(response.content)} bytes")

with zipfile.ZipFile(out_file, 'r') as z:
    media_files = [f for f in z.namelist() if f.startswith('xl/media/')]
    print("Embedded media files in XLSX:", media_files)
    assert len(media_files) >= 2, "Expected at least 2 embedded images"
    print("Embedded media verification SUCCESSFUL!")
