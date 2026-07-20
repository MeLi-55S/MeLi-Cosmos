from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.utils import timezone

from .models import Memo, Post, UserProfile, Notification

PLATFORM_DEFAULTS = {
    "name": "MeLi Cosmos",
    "title": "多用户博客平台",
    "avatar_seed": "MeLi",
    "avatar_url": "https://api.dicebear.com/7.x/bottts/png?seed=MeLi",
    "bio": "",
    "stats": [],
}


def profile(request):
    latest_memos = _get_latest_memos()

    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(
            user=request.user,
            defaults={"display_name": request.user.username},
        )

        avatar_src = (
            user_profile.avatar.url
            if user_profile.avatar
            else f"https://api.dicebear.com/7.x/bottts/png?seed={request.user.username}"
        )
        return {
            "profile": {
                "name": user_profile.display_name or request.user.username,
                "title": user_profile.title or "",
                "avatar_seed": user_profile.avatar_seed,
                "bio": user_profile.bio or "",
                "avatar_url": avatar_src,
                "website": user_profile.website,
                "github": user_profile.github,
                "github_username": _extract_github_username(user_profile.github),
                "email": request.user.email,
            },
            "latest_memos": latest_memos,
            "total_posts": Post.objects.filter(status="published").count(),
            "active_authors": _get_active_authors(),
            "unread_notifications_count": Notification.objects.filter(
                recipient=request.user, is_read=False
            ).count(),
        }

    return {
        "profile": PLATFORM_DEFAULTS,
        "latest_memos": latest_memos,
        "total_posts": Post.objects.filter(status="published").count(),
        "active_authors": _get_active_authors(),
        "unread_notifications_count": 0,
    }


def _get_latest_memos():
    """Return up to 5 memos from last 48h, at most 1 per author."""
    cutoff = timezone.now() - timedelta(hours=48)
    recent = (
        Memo.objects.filter(is_public=True, created_time__gte=cutoff)
        .select_related("author", "author__profile")
        .order_by("author", "-created_time")
    )
    seen = set()
    result = []
    for m in recent:
        if m.author_id not in seen:
            seen.add(m.author_id)
            result.append(m)
        if len(result) >= 5:
            break
    return result


def _extract_github_username(url):
    """Extract username from GitHub URL like https://github.com/username"""
    if not url:
        return ""
    try:
        return url.rstrip("/").split("/")[-1]
    except (IndexError, AttributeError):
        return ""


def _get_active_authors():
    return User.objects.annotate(
        post_count=Count("post", filter=Q(post__status="published"))
    ).filter(post_count__gt=0).select_related("profile").order_by("-post_count")[:5]
