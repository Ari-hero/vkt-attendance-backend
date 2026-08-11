from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from attendance_app.views import ApiRootView

def custom_404(request, exception=None):
    return JsonResponse({
        'error': 'Endpoint not found',
        'status': 404,
        'message': 'The requested API route does not exist. Please check / for available routes.'
    }, status=404)

def custom_500(request):
    return JsonResponse({
        'error': 'Internal server error',
        'status': 500,
        'message': 'An unexpected server error occurred.'
    }, status=500)

handler404 = custom_404
handler500 = custom_500

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', ApiRootView.as_view(), name='root-overview'),
    path('api/', include('attendance_app.urls')),
]
