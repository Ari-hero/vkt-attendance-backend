from django.db import migrations


def populate_employees_from_attendance_logs(apps, schema_editor):
    AttendanceLog = apps.get_model('attendance_app', 'AttendanceLog')
    Employee = apps.get_model('attendance_app', 'Employee')

    # Fetch all existing Employee emp_ids to ensure zero overwrites / duplicates
    existing_emp_ids = set(Employee.objects.values_list('emp_id', flat=True))

    # Fetch distinct emp_id from AttendanceLog ordered by newest timestamp
    historical_logs = AttendanceLog.objects.order_by('emp_id', '-timestamp')

    seen_ids = set(existing_emp_ids)
    employees_to_create = []

    for log in historical_logs:
        emp_id = str(log.emp_id).strip()
        if emp_id and emp_id not in seen_ids:
            seen_ids.add(emp_id)
            emp_name = log.emp_name.strip() if log.emp_name else f"Emp {emp_id}"
            employees_to_create.append(
                Employee(
                    emp_id=emp_id,
                    name=emp_name,
                    department="General",
                    embedding="[]",
                    photo_url="",
                )
            )

    if employees_to_create:
        Employee.objects.bulk_create(employees_to_create, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('attendance_app', '0003_last_seen_nullable'),
    ]

    operations = [
        migrations.RunPython(
            populate_employees_from_attendance_logs,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
