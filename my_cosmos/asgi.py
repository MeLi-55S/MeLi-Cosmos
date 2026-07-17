"""
ASGI config for MeLi Cosmos v2.0.
"""
from dotenv import load_dotenv
load_dotenv()

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "my_cosmos.settings")
application = get_asgi_application()
