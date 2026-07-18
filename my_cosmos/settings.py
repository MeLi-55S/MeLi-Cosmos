"""
Django settings for MeLi Cosmos v2.0.
Minimalist, production-ready configuration.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# Security
# =============================================================================
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-key-change-in-production-mycosmos-v2",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# =============================================================================
# Application Definition
# =============================================================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    # Project apps
    "blog",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "blog.middleware.BanCheckMiddleware",
]

ROOT_URLCONF = "my_cosmos.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "blog.context_processors.profile",
            ],
        },
    },
]

WSGI_APPLICATION = "my_cosmos.wsgi.application"

# =============================================================================
# Database
# =============================================================================
DATABASES = {
    "default": {
        "ENGINE": os.environ.get("DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.environ.get("DB_NAME", BASE_DIR / "db.sqlite3"),
    }
}

# =============================================================================
# Password Validation
# =============================================================================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =============================================================================
# Internationalization
# =============================================================================
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# =============================================================================
# Static & Media Files
# =============================================================================
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# =============================================================================
# Default Primary Key Field
# =============================================================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# Session & Security (respect X-Forwarded-For behind Nginx)
# =============================================================================
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 1209600  # 2 weeks
SESSION_SAVE_EVERY_REQUEST = True

# Auth
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/posts/"
LOGOUT_REDIRECT_URL = "/"

# Honor X-Forwarded-For header when behind trusted proxy (Nginx)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# =============================================================================
# View Counting
# =============================================================================
VIEW_LOG_COOLDOWN_HOURS = 1   # 同一指纹+IP 多久后可重新计数
VIEW_LOG_RETENTION_DAYS = 90  # 日志保留天数

# Markdown rendering configuration
MARKDOWN_EXTENSIONS = [
    "extra",
    "codehilite",
    "fenced_code",
    "toc",
    "nl2br",
]

# =============================================================================
# Server Identity
# =============================================================================
SERVER_DISPLAY_NAME = os.environ.get("SERVER_DISPLAY_NAME", "")  # 关于页面的服务器标识，留空则回退到 hostname
