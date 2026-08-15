import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

application = get_wsgi_application()

# Secure one-time password reset hook via environment variable
reset_pw = os.environ.get('RESET_ADMIN_PASSWORD')
if reset_pw:
    try:
        from django.contrib.auth.models import User
        user = User.objects.filter(username='admin').first()
        if user:
            user.set_password(reset_pw)
            user.save()
            print("[SECURITY] Admin password updated successfully via RESET_ADMIN_PASSWORD environment variable.")
        else:
            User.objects.create_superuser('admin', 'admin@example.com', reset_pw)
            print("[SECURITY] Admin superuser created successfully via RESET_ADMIN_PASSWORD environment variable.")
    except Exception as e:
        print(f"[SECURITY] Error updating admin password: {e}")

