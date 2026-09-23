"""ORM → API 响应体的转换层。

集中放在这里，避免每个 router 各写一份字段映射。渲染/文案/关联推荐等逻辑一律
复用 ``blog/views.py`` 的既有实现，不另起一套。
"""

from django.urls import reverse

from blog.views import (
    _format_like_display,
    _get_related_posts,
    _get_series_nav,
    _render_markdown,
)

from . import schema

READ_MINUTES_CHARS = 1000  # 与 blog.views.PostDetailView 的估算口径一致


def absolute(request, path):
    """把站内相对路径变成绝对 URL（移动端 WebView / 外链分享需要）。"""
    if not path:
        return ""
    if request is None:
        return path
    return request.build_absolute_uri(path)


def read_minutes(body_len):
    return max(1, round(body_len / READ_MINUTES_CHARS))


def author_ref(user):
    profile = getattr(user, "profile", None)
    return schema.AuthorRef(
        username=user.username,
        display_name=(profile.display_name if profile else "") or user.username,
        avatar_url=_avatar_url(profile),
    )


def _avatar_url(profile):
    if profile is None:
        return ""
    if profile.avatar:
        return profile.avatar.url
    return profile.avatar_url or ""


def category_ref(obj):
    if obj is None:
        return None
    return schema.CategoryRef(id=obj.pk, name=obj.name, slug=obj.slug)


def tag_ref(obj):
    return schema.TagRef(id=obj.pk, name=obj.name, slug=obj.slug)


def series_ref(obj):
    if obj is None:
        return None
    return schema.SeriesRef(id=obj.pk, name=obj.name, slug=obj.slug)


def post_url(post):
    return reverse("post_detail", kwargs={
        "username": post.author.username,
        "slug": post.slug,
    })


def memo_url(memo):
    return reverse("memo_detail", kwargs={
        "username": memo.author.username,
        "pk": memo.pk,
    })


def post_brief(request, post):
    return schema.PostBriefOut(
        unique_id=str(post.unique_id),
        slug=post.slug,
        title=post.title,
        excerpt=post.excerpt or "",
        cover=post.cover or "",
        status=post.status,
        license=post.license,
        views=post.views,
        read_minutes=read_minutes(len(post.body or "")),
        url=absolute(request, post_url(post)),
        created_time=post.created_time,
        modified_time=post.modified_time,
        author=author_ref(post.author),
        category=category_ref(post.category),
        series=series_ref(post.series),
        tags=[tag_ref(t) for t in post.tags.all()],
    )


def _like_state(request, obj):
    from django.contrib.contenttypes.models import ContentType

    from blog.models import Like

    ct = ContentType.objects.get_for_model(obj)
    likes = Like.objects.filter(content_type=ct, object_id=obj.pk).select_related("user__profile")
    count = likes.count()
    names = [
        l.user.profile.display_name or l.user.username
        for l in likes.order_by("-created_time")[:2]
    ]
    user = getattr(request, "user", None)
    liked = bool(
        user is not None
        and user.is_authenticated
        and Like.objects.filter(user=user, content_type=ct, object_id=obj.pk).exists()
    )
    return schema.LikeState(
        count=count,
        display_text=_format_like_display(names, count),
        user_liked=liked,
    )


def _comments_count(obj):
    from django.contrib.contenttypes.models import ContentType

    from blog.models import Comment

    ct = ContentType.objects.get_for_model(obj)
    return Comment.objects.filter(content_type=ct, object_id=obj.pk, is_visible=True).count()


def _series_nav_item(request, post):
    return schema.SeriesNavItem(
        id=post.pk,
        slug=post.slug,
        title=post.title,
        url=absolute(request, post_url(post)),
        series_order=post.series_order,
    )


def post_detail(request, post):
    body_html, toc_html = _render_markdown(post.body)
    nav = _get_series_nav(post)
    return schema.PostDetailOut(
        **post_brief(request, post).model_dump(),
        body=post.body,
        body_html=body_html,
        toc_html=toc_html if "<li>" in toc_html else "",
        likes=_like_state(request, post),
        comments_count=_comments_count(post),
        related=[post_brief(request, p) for p in _get_related_posts(post)],
        series_nav=(
            schema.SeriesNav(
                series=series_ref(nav["series"]),
                index=nav["index"],
                total=nav["total"],
                prev=_series_nav_item(request, nav["prev"]) if nav.get("prev") else None,
                next=_series_nav_item(request, nav["next"]) if nav.get("next") else None,
            )
            if nav
            else None
        ),
    )


def memo_out(request, memo):
    return schema.MemoOut(
        id=memo.pk,
        content=memo.content,
        is_public=memo.is_public,
        url=absolute(request, memo_url(memo)),
        author=author_ref(memo.author),
        created_time=memo.created_time,
        likes=_like_state(request, memo),
        comments_count=_comments_count(memo),
    )


def comment_out(request, comment):
    owner = getattr(comment, "user", None)
    is_visible = comment.is_visible
    return schema.CommentOut(
        id=comment.pk,
        content=comment.content,
        display_name=comment.display_name,
        avatar_url=comment.display_avatar,
        is_guest=owner is None,
        is_visible=is_visible,
        is_pending=not is_visible,
        created_time=comment.created_time,
        content_type=f"{comment.content_type.app_label}.{comment.content_type.model}",
        object_id=comment.object_id,
    )


def notification_out(request, notification):
    return schema.NotificationOut(
        id=notification.pk,
        notification_type=notification.notification_type,
        type_display=notification.get_notification_type_display(),
        message=notification.message,
        is_read=notification.is_read,
        created_time=notification.created_time,
        target_url=absolute(request, notification.get_target_url()),
        content_type=f"{notification.content_type.app_label}.{notification.content_type.model}",
        object_id=notification.object_id,
        actor=author_ref(notification.actor) if notification.actor else None,
    )


def user_public(request, user, stats=None):
    profile = user.profile
    return schema.UserPublicOut(
        username=user.username,
        display_name=profile.display_name or user.username,
        title=profile.title or "",
        bio=profile.bio or "",
        avatar_url=_avatar_url(profile),
        website=profile.website or "",
        github=profile.github or "",
        mastodon=profile.mastodon or "",
        url=absolute(request, reverse("user_space", kwargs={"username": user.username})),
        joined_at=user.date_joined,
        stats=stats if stats is not None else user_space_stats(user),
    )


def user_space_stats(user):
    from django.db.models import Sum

    from blog.models import Memo, Post

    published = Post.objects.filter(author=user, status="published")
    return {
        "total_posts": Post.objects.filter(author=user).count(),
        "published_count": published.count(),
        "total_views": published.aggregate(total=Sum("views"))["total"] or 0,
        "public_memos": Memo.objects.filter(author=user, is_public=True).count(),
    }


def paginate(request, queryset, page, page_size, mapper):
    """统一的分页信封。``mapper`` 接收单个对象，返回 Schema 实例。"""
    from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

    paginator = Paginator(queryset, page_size)
    try:
        current = paginator.page(page)
    except PageNotAnInteger:
        current = paginator.page(1)
    except EmptyPage:
        current = paginator.page(paginator.num_pages)

    return {
        "count": paginator.count,
        "page": current.number,
        "page_size": page_size,
        "total_pages": paginator.num_pages,
        "results": [mapper(obj) for obj in current.object_list],
    }


def token_out(api_token):
    return schema.TokenOut(
        id=api_token.pk,
        name=api_token.name,
        prefix=api_token.prefix,
        created_at=api_token.created_at,
        last_used_at=api_token.last_used_at,
        revoked_at=api_token.revoked_at,
    )
