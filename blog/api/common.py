"""跨 router 复用的工具：分页、目标解析、可见性判断、表单校验。

放在这里的东西必须同时服务于 ``routers/`` 下的多个模块；某个 router 独有的逻辑
留在它自己的文件里，不要提前抽象。
"""

from uuid import UUID

from django.db.models import Q

from blog.models import Category, Post, Series, Tag

from .errors import APIError, forbidden, not_found

PAGE_SIZE_MAX = 100
PAGE_SIZE_DEFAULT = 20

VALID_STATUS = {choice[0] for choice in Post.STATUS_CHOICES}
VALID_LICENSE = {choice[0] for choice in Post.LICENSE_CHOICES}
ORDER_FIELDS = {
    "modified": "-modified_time",
    "created": "-created_time",
    "views": "-views",
    "title": "title",
}

#: 分类法端点支持的三种条目类型
TAXONOMY_MODELS = {"category": Category, "tag": Tag, "series": Series}


def clamp_page_size(page_size):
    return max(1, min(int(page_size or PAGE_SIZE_DEFAULT), PAGE_SIZE_MAX))


def current_user(request):
    """已登录用户或 None（API 里 request.user 可能是 AnonymousUser）。"""
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def require_user(request, message="需要登录"):
    user = current_user(request)
    if user is None:
        raise forbidden(message)
    return user


def is_author(request, obj):
    user = current_user(request)
    return bool(user is not None and getattr(obj, "author_id", None) == user.pk)


def require_author(request, obj, message="只能操作自己的内容"):
    if not is_author(request, obj):
        raise forbidden(message)
    return obj


def post_queryset():
    return Post.objects.select_related(
        "author__profile", "category", "series"
    ).prefetch_related("tags")


def visible_filter(request):
    """与 Web 首页同一口径的可见性条件：匿名只见已发布，登录用户额外看到自己的。"""
    visible = Q(status="published")
    user = current_user(request)
    if user is not None:
        visible |= Q(author=user)
    return visible


def visible_posts(request):
    return Post.objects.filter(visible_filter(request))


def validate_status(status):
    if status not in VALID_STATUS:
        raise APIError(422, "validation_error", "status 只能是 draft / published / private")


def validation_error(details, message):
    err = APIError(422, "validation_error", message)
    err.details = details
    return err


def run_form(form, error_message="表单校验失败"):
    """跑既有 Django Form 校验，失败时转成统一的 422（details 为字段级错误）。"""
    if form.is_valid():
        return form
    details = {field: [str(e) for e in errs] for field, errs in form.errors.items()}
    messages = [e for errs in details.values() for e in errs]
    raise validation_error(details, messages[0] if messages else error_message)


# ── 文章定位（unique_id 或 slug）────────────────────────────────────────


def resolve_post(ref, request=None, author=None):
    """按 ``unique_id`` 或 ``slug`` 取文章。

    slug 在站内是"每作者唯一"，跨作者可能重复：``?author=`` 可消歧，
    确实存在多篇同名时返回 409 而不是随便挑一篇。
    """
    qs = post_queryset()
    if author:
        qs = qs.filter(author__username=author)

    try:
        UUID(str(ref))
    except (ValueError, AttributeError, TypeError):
        matches = list(qs.filter(slug=ref)[:2])
        if not matches:
            raise not_found("文章不存在")
        if len(matches) > 1:
            raise APIError(
                409, "ambiguous_slug",
                "该 slug 对应多位作者的文章，请改用 unique_id 或附加 ?author=<用户名>",
            )
        post = matches[0]
    else:
        post = qs.filter(unique_id=ref).first()
        if post is None:
            raise not_found("文章不存在")

    if post.status != "published" and not (request is not None and is_author(request, post)):
        raise not_found("文章不存在")
    return post


# ── 分类法（分类 / 标签 / 系列）─────────────────────────────────────────


def taxonomy_model(kind):
    model_cls = TAXONOMY_MODELS.get(kind)
    if model_cls is None:
        raise APIError(422, "validation_error",
                       "kind 只能是 category / tag / series")
    return model_cls


def taxonomy_items(model_cls, request, author=None, with_counts=True):
    """列出"名下有可见文章"的分类/标签/系列，登录用户额外看到自己尚未使用的条目。

    Web 侧边栏按当前用户能读到的文章聚合，这里口径一致：匿名只看到挂有已发布
    文章的条目。分类是个位数量级的集合，逐条计数换取可读性。
    """
    field = _post_relation(model_cls)
    user = current_user(request)
    posts = visible_posts(request)
    candidates = model_cls.objects.select_related("author")
    if author:
        posts = posts.filter(author__username=author)
        candidates = candidates.filter(author__username=author)

    results = []
    for obj in candidates.order_by("name"):
        count = posts.filter(**{field: obj}).count()
        is_own = user is not None and obj.author_id == user.pk
        if not count and not is_own:
            continue
        item = {"id": obj.pk, "name": obj.name, "slug": obj.slug}
        if with_counts:
            item["post_count"] = count
        if model_cls is Series:
            item["description"] = obj.description or ""
            item["author"] = obj.author.username
        results.append(item)
    return results


def _post_relation(model_cls):
    return {Category: "category", Tag: "tags", Series: "series"}[model_cls]


def taxonomy_exists(model_cls, pk, request):
    obj = model_cls.objects.filter(pk=pk).select_related("author").first()
    if obj is None:
        raise not_found("条目不存在")
    user = current_user(request)
    if user is not None and obj.author_id == user.pk:
        return obj
    # 他人的条目只在"挂着已发布文章"时可见
    field = _post_relation(model_cls)
    if not Post.objects.filter(**{field: obj}, status="published").exists():
        raise not_found("条目不存在")
    return obj
