from rest_framework import serializers
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from .models import Device, Employee, AttendanceLog


def _device_status(last_seen):
    """
    Returns one of three canonical status strings based on last_seen.
    Threshold is sourced from settings.DEVICE_ONLINE_THRESHOLD_MINUTES.
    """
    if last_seen is None:
        return 'NEVER_SEEN'
    threshold = timedelta(minutes=getattr(settings, 'DEVICE_ONLINE_THRESHOLD_MINUTES', 10))
    if (timezone.now() - last_seen) <= threshold:
        return 'ONLINE'
    return 'OFFLINE'


class DeviceSerializer(serializers.ModelSerializer):
    """
    Exposes device identity and live connectivity status.
    api_key is deliberately excluded — never returned to the dashboard.
    """
    status = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = ['id', 'device_id', 'name', 'is_active', 'last_seen', 'created_at', 'status']

    def get_status(self, obj):
        return _device_status(obj.last_seen)


class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = '__all__'


class AttendanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceLog
        fields = '__all__'
