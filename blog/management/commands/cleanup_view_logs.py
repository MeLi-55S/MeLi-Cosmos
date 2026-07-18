"""
Cleanup old ViewLog entries. Run via cron daily.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from blog.models import ViewLog


class Command(BaseCommand):
    help = "Delete ViewLog entries older than VIEW_LOG_RETENTION_DAYS (default 90)."

    def handle(self, *args, **options):
        retention = getattr(settings, "VIEW_LOG_RETENTION_DAYS", 90)
        cutoff = timezone.now() - timedelta(days=retention)
        qs = ViewLog.objects.filter(created_at__lt=cutoff)
        count = qs.count()
        qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Cleaned up {count} old ViewLog entries."))
