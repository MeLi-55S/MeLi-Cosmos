"""站点级信息与健康检查端点（匿名可读）。"""

from django import get_version
from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone
from ninja import Router

from blog.models import Comment, Memo, Post

from ..schema import HealthOut, SiteMetaOut

meta_router = Router()

API_VERSION = "v1"
SITE_NAME = getattr(settings, "SITE_NAME", "MeLi Cosmos")


@meta_router.get("/health", response=HealthOut, summary="健康检查")
def health(request):
    """探针用：确认进程活着且能查询数据库。"""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database = "ok"
    except Exception:  # pragma: no cover - 只在数据库故障时走到
        database = "error"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "server_time": timezone.now(),
        "django_version": get_version(),
        "api_version": API_VERSION,
    }


@meta_router.get("/meta", response=SiteMetaOut, summary="站点与 API 元信息")
def site_meta(request):
    """移动端启动时拉一次：站点名、总量统计、认证方式与能力开关。"""
    published = Post.objects.filter(status="published")
    base = request.build_absolute_uri("/")
    return {
        "site_name": SITE_NAME,
        "display_name": SITE_NAME,
        "base_url": base,
        "api_version": API_VERSION,
        "docs_url": request.build_absolute_uri("/api/v1/docs"),
        "totals": {
            "posts": published.count(),
            "memos": Memo.objects.filter(is_public=True).count(),
            "comments": Comment.objects.filter(is_visible=True).count(),
            "users": User.objects.filter(is_active=True).count(),
        },
        "auth": {
            "type": "bearer",
            "header": "Authorization: Bearer <token>",
            "token_prefix": "mlc_",
            "register_requires_invite": True,
            "invite_code_expire_hours": getattr(settings, "INVITE_CODE_EXPIRE_HOURS", 24),
            "invite_daily_limit": getattr(settings, "INVITE_DAILY_LIMIT", 1),
            "comment_rate_limit_per_hour": getattr(settings, "COMMENT_RATE_LIMIT", 5),
            "comment_max_length": getattr(settings, "COMMENT_MAX_LENGTH", 2000),
        },
        "features": [
            "posts.read", "posts.write", "memos.read", "memos.write",
            "comments.read", "comments.write",
            "likes.write", "inbox.read", "inbox.write",
            "taxonomies.write", "uploads.images", "uploads.avatar",
            "tokens.manage", "profile.write", "invite.generate",
        ],
    }
