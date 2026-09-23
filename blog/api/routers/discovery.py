"""发现类只读端点：用户、时间归档、跨类型搜索。

全部匿名可读（带令牌时结果会多出本人的非公开内容）。可见性口径与 Web 一致：
文章只算 ``published``，碎碎念只算 ``is_public``，用户只算已激活且未被封禁。
"""

from collections import OrderedDict
from typing import Optional

from django.contrib.auth.models import User
from django.db.models import Count, Q
from ninja import Router

from blog.models import Category, Memo, Series, Tag

from ..auth import OptionalBearerAuth
from ..common import (
    clamp_page_size,
    current_user,
    post_queryset,
    taxonomy_items,
    visible_filter,
)
from ..errors import not_found
from ..schema import ArchiveListOut, SearchOut, UserListOut, UserPublicOut
from ..serializers import memo_out, paginate, post_brief, user_public, user_space_stats

discovery_router = Router()

#: 用户列表的排序口径
USER_ORDER = {
    "joined": "-date_joined",
    "username": "username",
    "posts": "-published_count",
}

#: 搜索结果里每一类的默认条数
SEARCH_PER_TYPE = 5

TAXONOMY_KINDS = (
    ("category", Category, "categories"),
    ("tag", Tag, "tags"),
    ("series", Series, "series"),
)


def active_users():
    """站内可见用户：账号已激活且未被封禁。"""
    return (User.objects
            .filter(is_active=True, profile__is_banned=False)
            .select_related("profile"))


def visible_memos(request):
    """碎碎念可见性：公开的 + 本人的（带令牌时）。

    注意 Web 的 ``/memos/`` 会把他人非公开碎碎念泄漏给任意登录用户，
    API 刻意不复制这个缺陷。
    """
    user = current_user(request)
    scope = Q(is_public=True) | Q(author=user) if user is not None else Q(is_public=True)
    return Memo.objects.select_related("author__profile").filter(scope)


@discovery_router.get(
    "/users", response=UserListOut, auth=OptionalBearerAuth(),
    summary="用户列表",
    description="排序：``joined``（默认，最新加入）/ ``username`` / ``posts``（按已发布文章数）。",
)
def list_users(request, q: Optional[str] = None, order: str = "joined",
               page: int = 1, page_size: int = 20):
    qs = active_users().annotate(
        published_count=Count("post", filter=Q(post__status="published"), distinct=True)
    )
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(profile__display_name__icontains=q))
    qs = qs.order_by(USER_ORDER.get(order, USER_ORDER["joined"]))
    return paginate(request, qs, page, clamp_page_size(page_size),
                    lambda user: user_public(request, user))


@discovery_router.get(
    "/users/{username}", response=UserPublicOut, auth=OptionalBearerAuth(),
    summary="用户公开资料",
    description="含 ``stats``：文章总数、已发布数、总浏览量、公开碎碎念数。",
)
def get_user(request, username: str):
    user = active_users().filter(username=username).first()
    if user is None:
        raise not_found("用户不存在")
    return user_public(request, user, user_space_stats(user))


@discovery_router.get(
    "/archives", response=ArchiveListOut, auth=OptionalBearerAuth(),
    summary="时间归档",
    description="按年月分组的已发布文章，口径与 Web 的 ``/archives/`` 相同；"
                "``author`` 限定某位作者，``year`` / ``month`` 精确到某个月。",
)
def archives(request, author: Optional[str] = None,
             year: Optional[int] = None, month: Optional[int] = None):
    qs = post_queryset().filter(status="published")
    if author:
        qs = qs.filter(author__username=author)
    if year:
        qs = qs.filter(created_time__year=year)
    if month:
        qs = qs.filter(created_time__month=month)

    buckets = OrderedDict()
    for post in qs.order_by("-created_time"):
        buckets.setdefault((post.created_time.year, post.created_time.month), []).append(post)

    results = [
        {
            "year": y,
            "month": m,
            "label": f"{y} 年 {m} 月",
            "count": len(posts),
            "posts": [post_brief(request, p) for p in posts],
        }
        for (y, m), posts in buckets.items()
    ]
    return {"count": sum(item["count"] for item in results), "buckets": results}


@discovery_router.get(
    "/search", response=SearchOut, auth=OptionalBearerAuth(),
    summary="全站搜索",
    description="与 Web 搜索一样按标题/正文 ``icontains`` 匹配文章，另外附带命中的碎碎念、用户与"
                "分类法条目。``types`` 逗号分隔可只搜其中几类：post,memo,user,category,tag,series。",
)
def search(request, q: str = "", types: Optional[str] = None,
           limit: int = SEARCH_PER_TYPE, author: Optional[str] = None):
    term = (q or "").strip()
    size = max(1, min(int(limit or SEARCH_PER_TYPE), 20))
    wanted = {t.strip().lower() for t in (types or "").split(",") if t.strip()}

    def want(kind):
        return not wanted or kind in wanted

    body = {"query": term, "posts": [], "memos": [], "users": [],
            "categories": [], "tags": [], "series": []}
    if not term:
        return body

    if want("post"):
        qs = (post_queryset().filter(visible_filter(request))
              .filter(Q(title__icontains=term) | Q(body__icontains=term)).distinct())
        if author:
            qs = qs.filter(author__username=author)
        body["posts"] = [post_brief(request, p) for p in qs[:size]]

    if want("memo"):
        qs = visible_memos(request).filter(content__icontains=term)
        if author:
            qs = qs.filter(author__username=author)
        body["memos"] = [memo_out(request, m) for m in qs[:size]]

    if want("user") and not author:
        users = active_users().filter(
            Q(username__icontains=term) | Q(profile__display_name__icontains=term)
        )[:size]
        body["users"] = [user_public(request, u) for u in users]

    if not wanted or any(want(kind) for kind, _, _ in TAXONOMY_KINDS):
        by_kind = {}
        for kind, model_cls, _ in TAXONOMY_KINDS:
            if want(kind):
                by_kind[kind] = taxonomy_items(model_cls, request, author)
        for kind, _, key in TAXONOMY_KINDS:
            items = by_kind.get(kind)
            if items is None:
                continue
            body[key] = [
                item for item in items
                if term.lower() in item["name"].lower() or term.lower() in item["slug"].lower()
            ][:size]

    return body
