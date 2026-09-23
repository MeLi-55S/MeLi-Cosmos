"""文章与碎碎念端点。

读端点匿名可用；写端点需要 Bearer 令牌。校验一律走 ``blog.forms`` 里的既有表单，
标签解析、slug 生成、字段长度等规则与 Web 端保持同一份实现。
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from django.db.models import Q
from django.db.utils import IntegrityError
from django.utils import timezone
from ninja import Router

from blog.forms import MemoForm, PostForm
from blog.models import Memo, Post

from ..auth import BearerAuth, OptionalBearerAuth
from ..errors import APIError, conflict, forbidden, not_found
from ..schema import (
    MemoIn,
    MemoListOut,
    MemoOut,
    OkOut,
    PostDetailOut,
    PostIn,
    PostListOut,
)
from ..serializers import memo_out, paginate, post_brief, post_detail
from ..auth import BearerAuth, OptionalBearerAuth

posts_router = Router()
memos_router = Router()

PAGE_SIZE_MAX = 100
VALID_STATUS = {choice[0] for choice in Post.STATUS_CHOICES}
VALID_LICENSE = {choice[0] for choice in Post.LICENSE_CHOICES}
ORDER_FIELDS = {
    "modified": "-modified_time",
    "created": "-created_time",
    "views": "-views",
    "title": "title",
}


def _clamp_page_size(page_size):
    return max(1, min(int(page_size or 20), PAGE_SIZE_MAX))


def _post_queryset():
    return Post.objects.select_related(
        "author__profile", "category", "series"
    ).prefetch_related("tags")


def _is_author(request, post):
    user = getattr(request, "user", None)
    return bool(user is not None and user.is_authenticated and post.author_id == user.pk)


def resolve_post(ref, request=None, author=None):
    """按 ``unique_id`` 或 ``slug`` 取文章。

    slug 在站内是"每作者唯一"，跨作者可能重复：``?author=`` 可消歧，
    确实存在多篇同名时返回 409 而不是随便挑一篇。
    """
    qs = _post_queryset()
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

    if post.status != "published" and not (request is not None and _is_author(request, post)):
        raise not_found("文章不存在")
    return post


def _require_author(request, post):
    if not _is_author(request, post):
        raise forbidden("只能修改自己的文章")
    return post


def _validate_status(status):
    if status not in VALID_STATUS:
        raise APIError(422, "validation_error", "status 只能是 draft / published / private")


def _check_enums(data: "PostIn"):
    if data.status:
        _validate_status(data.status)
    if data.license and data.license not in VALID_LICENSE:
        raise APIError(422, "validation_error", "license 不在允许的取值范围内")


def _form_payload(data: PostIn, instance=None):
    """PostIn → PostForm 所需的表单字典（更新时以现有值兜底）。"""
    base = {
        "title": getattr(instance, "title", "") if instance else "",
        "cover": getattr(instance, "cover", "") if instance else "",
        "body": getattr(instance, "body", "") if instance else "",
        "excerpt": getattr(instance, "excerpt", "") if instance else "",
        "category": getattr(instance, "category_id", "") if instance else "",
        "series": getattr(instance, "series_id", "") if instance else "",
        "license": getattr(instance, "license", "") if instance else "CC BY-NC 4.0",
        "status": getattr(instance, "status", "draft") if instance else "draft",
        "tag_names": "",
    }
    if instance is not None:
        base["tag_names"] = " ".join(instance.tags.values_list("name", flat=True))

    provided = data.model_dump(exclude_unset=True, exclude_none=True)
    if "tag_names" in provided:
        base["tag_names"] = " ".join(
            t.strip() for t in provided.pop("tag_names") if t and t.strip()
        )
    key_map = {"category_id": "category", "series_id": "series"}
    for key, value in provided.items():
        base[key_map.get(key, key)] = value
    return base


def _apply_series_order(post, data: PostIn):
    """``series_order`` 不在 PostForm 字段里，单独校验并落库。"""
    if "series_order" not in data.model_fields_set:
        return
    value = data.series_order
    if value is None or not 1 <= int(value) <= 9999:
        raise APIError(422, "validation_error", "series_order 需为 1-9999 的整数")
    if int(value) != post.series_order:
        post.series_order = int(value)
        post.save(update_fields=["series_order"])


def _validation_error(details, message):
    err = APIError(422, "validation_error", message)
    err.details = details
    return err


def _run_form(form, error_message="表单校验失败"):
    """跑既有 Django Form 校验，失败时转成统一的 422。"""
    if form.is_valid():
        return form
    details = {field: [str(e) for e in errs] for field, errs in form.errors.items()}
    messages = [e for errs in details.values() for e in errs]
    raise _validation_error(details, messages[0] if messages else error_message)


# ── 文章：读 ────────────────────────────────────────────────────────────


@posts_router.get(
    "",
    response=PostListOut,
    auth=OptionalBearerAuth(),
    summary="文章列表",
    description=(
        "只返回已发布文章；作者本人用 ``mine=true`` 检索自己的草稿与私密文章，"
        "可再用 ``status`` 精确过滤。"
    ),
)
def list_posts(
    request,
    author: Optional[str] = None,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    series: Optional[str] = None,
    q: Optional[str] = None,
    mine: bool = False,
    status: Optional[str] = None,
    order: str = "modified",
    since: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
):
    qs = _post_queryset()
    user = getattr(request, "user", None)
    is_authed = user is not None and user.is_authenticated

    if mine:
        if not is_authed:
            raise forbidden("mine=true 需要携带作者本人的令牌")
        qs = qs.filter(author=user)
        if status:
            _validate_status(status)
            qs = qs.filter(status=status)
    else:
        # 公开口径：只有已发布文章可被列表检索，草稿/私密一律走 mine=true
        qs = qs.filter(status="published")

    if author:
        qs = qs.filter(author__username=author)
    if category:
        qs = qs.filter(category__slug=category)
    if tag:
        qs = qs.filter(tags__slug=tag)
    if series:
        qs = qs.filter(series__slug=series)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    if since:
        qs = qs.filter(modified_time__gt=since)

    qs = qs.order_by(ORDER_FIELDS.get(order, ORDER_FIELDS["modified"])).distinct()
    return paginate(request, qs, page, _clamp_page_size(page_size),
                    lambda post: post_brief(request, post))


@posts_router.get(
    "/{ref}",
    response=PostDetailOut,
    auth=OptionalBearerAuth(),
    summary="文章详情",
    description="``ref`` 可为 ``unique_id``（推荐）或 ``slug``；slug 重复时用 ``?author=`` 消歧。",
)
def get_post(request, ref: str, author: Optional[str] = None):
    post = resolve_post(ref, request, author)
    return post_detail(request, post)


# ── 文章：写 ────────────────────────────────────────────────────────────


@posts_router.post(
    "",
    response={201: PostDetailOut, 409: None},
    auth=BearerAuth(),
    summary="创建文章",
)
def create_post(request, data: PostIn):
    user = request.user
    _check_enums(data)

    payload = _form_payload(data)
    form = _run_form(PostForm(data=payload, user=user, instance=Post(author=user)))
    try:
        post = form.save()
        _apply_series_order(post, data)
    except IntegrityError:
        raise conflict("你已有一篇相同标题（或相同 URL 别名）的文章，请修改标题。")
    post = _post_queryset().get(pk=post.pk)
    return 201, post_detail(request, post)


@posts_router.patch(
    "/{ref}",
    response=PostDetailOut,
    auth=BearerAuth(),
    summary="更新文章（可作自动保存）",
    description="只提交需要改动的字段；语义与 Web 端 ``/ajax/post/autosave/`` 一致。",
)
def update_post(request, ref: str, data: PostIn):
    post = _require_author(request, resolve_post(ref, request))
    _check_enums(data)

    payload = _form_payload(data, instance=post)
    form = _run_form(PostForm(data=payload, user=request.user, instance=post))
    try:
        saved = form.save()
        _apply_series_order(saved, data)
    except IntegrityError:
        raise conflict("你已有一篇相同标题（或相同 URL 别名）的文章，请修改标题。")
    return post_detail(request, _post_queryset().get(pk=saved.pk))


@posts_router.post(
    "/{ref}/publish",
    response=PostDetailOut,
    auth=BearerAuth(),
    summary="发布文章",
    description="与 Web 端 ``/post/<uuid>/publish/`` 相同：置为 published 并把发布时间刷到当前。",
)
def publish_post(request, ref: str):
    post = _require_author(request, resolve_post(ref, request))
    post.status = "published"
    post.created_time = timezone.now()
    post.save(update_fields=["status", "created_time"])
    return post_detail(request, _post_queryset().get(pk=post.pk))


@posts_router.delete(
    "/{ref}",
    response=OkOut,
    auth=BearerAuth(),
    summary="删除文章",
)
def delete_post(request, ref: str):
    post = _require_author(request, resolve_post(ref, request))
    post.delete()
    return {"ok": True, "detail": "文章已删除"}


# ── 碎碎念 ──────────────────────────────────────────────────────────────


@memos_router.get(
    "",
    response=MemoListOut,
    auth=OptionalBearerAuth(),
    summary="碎碎念列表",
    description=(
        "返回公开碎碎念；带令牌时额外返回本人的非公开内容。"
        "注意：Web 端 ``/memos/`` 会让任意登录用户看到他人的非公开碎碎念，"
        "API 刻意不复制这一行为。"
    ),
)
def list_memos(
    request,
    author: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    qs = Memo.objects.select_related("author__profile")
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        qs = qs.filter(Q(is_public=True) | Q(author=user))
    else:
        qs = qs.filter(is_public=True)
    if author:
        qs = qs.filter(author__username=author)
    return paginate(request, qs.order_by("-created_time"), page,
                    _clamp_page_size(page_size), lambda memo: memo_out(request, memo))


@memos_router.get(
    "/{memo_id}",
    response=MemoOut,
    auth=OptionalBearerAuth(),
    summary="碎碎念详情",
)
def get_memo(request, memo_id: int):
    memo = Memo.objects.select_related("author__profile").filter(pk=memo_id).first()
    if memo is None:
        raise not_found("碎碎念不存在")
    if not memo.is_public and not _is_author(request, memo):
        raise not_found("碎碎念不存在")
    return memo_out(request, memo)


@memos_router.post(
    "",
    response={201: MemoOut},
    auth=BearerAuth(),
    summary="发布碎碎念",
)
def create_memo(request, data: MemoIn):
    form = _run_form(MemoForm(data={"content": data.content, "is_public": data.is_public}))
    memo = form.save(commit=False)
    memo.author = request.user
    memo.save()
    return 201, memo_out(request, memo)


@memos_router.delete(
    "/{memo_id}",
    response=OkOut,
    auth=BearerAuth(),
    summary="删除碎碎念",
)
def delete_memo(request, memo_id: int):
    memo = Memo.objects.filter(pk=memo_id).first()
    if memo is None:
        raise not_found("碎碎念不存在")
    if memo.author_id != request.user.pk:
        raise forbidden("只能删除自己的碎碎念")
    memo.delete()
    return {"ok": True, "detail": "碎碎念已删除"}
