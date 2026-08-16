from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from attendance_app.models import AttendanceLog, Employee, Device

class Command(BaseCommand):
    help = 'Purges AttendanceLog records older than 45 days (retention policy). Preserves employees, devices, and config.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate purge calculation without deleting any records.',
        )
        parser.add_argument(
            '--days',
            type=int,
            default=45,
            help='Number of days threshold for retention (default: 45).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        days = options['days']

        now = timezone.now()
        cutoff_date = (now - timedelta(days=days)).date()

        eligible_query = AttendanceLog.objects.filter(date__lt=cutoff_date)
        count = eligible_query.count()

        oldest_log = eligible_query.order_by('date', 'timestamp').first()
        newest_log = eligible_query.order_by('-date', '-timestamp').first()

        self.stdout.write(self.style.MIGRATE_HEADING("=== ATTENDANCE 45-DAY DATA RETENTION POLICY ==="))
        self.stdout.write(f"Current Date/Time (UTC): {now.isoformat()}")
        self.stdout.write(f"Retention Threshold: {days} days")
        self.stdout.write(f"Strict Cutoff Date: {cutoff_date.isoformat()} (Records before this date are eligible)")
        self.stdout.write(f"Eligible AttendanceLog records: {count}")

        if oldest_log:
            self.stdout.write(f"Oldest eligible record date: {oldest_log.date} (UUID: {oldest_log.uuid})")
            self.stdout.write(f"Newest eligible record date: {newest_log.date} (UUID: {newest_log.uuid})")
        else:
            self.stdout.write("No AttendanceLog records older than the cutoff threshold were found.")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN ONLY] No database records were modified or deleted."))
            return

        if count > 0:
            deleted_count, _ = eligible_query.delete()
            self.stdout.write(self.style.SUCCESS(f"\n[SUCCESS] Successfully purged {deleted_count} AttendanceLog records older than {cutoff_date}."))
        else:
            self.stdout.write(self.style.SUCCESS("\n[SUCCESS] Database already compliant. Zero records deleted."))
