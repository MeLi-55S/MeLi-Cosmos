"""
ASGI config for Project MyCosmos v2.0.
"""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "my_cosmos.settings")
application = get_asgi_application()
