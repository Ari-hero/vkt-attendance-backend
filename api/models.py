import uuid
from django.db import models
from django.utils import timezone

class Device(models.Model):
    device_id = models.CharField(max_length=100, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    api_key = models.CharField(max_length=100, unique=True, db_index=True, default=uuid.uuid4)
    is_active = models.BooleanField(default=True, db_index=True)
    last_seen = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.device_id})"


class Employee(models.Model):
    emp_id = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=200, db_index=True)
    department = models.CharField(max_length=100, default="General")
    photo_url = models.TextField(blank=True, null=True)
    embedding = models.TextField(help_text="JSON serialized 128 float array")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.emp_id})"


class AttendanceLog(models.Model):
    uuid = models.CharField(max_length=100, unique=True, db_index=True)
    emp_id = models.CharField(max_length=50, db_index=True)
    emp_name = models.CharField(max_length=200)
    timestamp = models.DateTimeField(db_index=True)
    date = models.DateField(db_index=True)
    time = models.TimeField()
    type = models.CharField(max_length=10, choices=[('IN', 'IN'), ('OUT', 'OUT')])
    confidence = models.FloatField(default=0.0)
    device_id = models.CharField(max_length=100, default="default_kiosk", db_index=True)
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['emp_id', 'timestamp']),
            models.Index(fields=['date', 'emp_id']),
            models.Index(fields=['uuid']),
        ]

    def __str__(self):
        return f"{self.emp_name} ({self.emp_id}) - {self.type} at {self.timestamp}"
