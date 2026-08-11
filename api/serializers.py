from rest_framework import serializers
from .models import Device, Employee, AttendanceLog

class DeviceSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = ['id', 'device_id', 'name', 'api_key', 'is_active', 'last_seen', 'created_at', 'status']

    def get_status(self, obj):
        from django.utils import timezone
        from datetime import timedelta
        if obj.last_seen and (timezone.now() - obj.last_seen) < timedelta(minutes=15):
            return 'ONLINE'
        return 'OFFLINE'

class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = '__all__'

class AttendanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceLog
        fields = '__all__'
