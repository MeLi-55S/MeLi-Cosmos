"""
WSGI config for Project MyCosmos v2.0.
Production: Gunicorn -> my_cosmos.wsgi:application
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "my_cosmos.settings")
application = get_wsgi_application()
