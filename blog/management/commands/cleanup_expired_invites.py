"""
Cleanup expired invite codes. Run via cron daily.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from blog.models import InviteCode


class Command(BaseCommand):
    help = "Delete expired invite codes that have never been used."

    def handle(self, *args, **options):
        qs = InviteCode.objects.filter(is_used=False, expires_at__lt=timezone.now())
        count = qs.count()
        qs.delete()
        self.stdout.write(f"Cleaned up {count} expired invite codes.")
