"""分类法端点：分类 / 标签 / 系列。

读端点匿名可用，只列出"名下有可见文章"的条目（与 Web 侧边栏一致）；
写端点只允许创建——站内没有改名与删除的界面（那属于 Django admin），
API 也就不新造这套权限。创建按 ``slugify(name)`` 幂等：同名返回已有条目，
状态码 200；确实新建时才返回 201，移动端重试因此是安全的。
"""

from typing import Optional

from ninja import Router

from blog.forms import SeriesForm
from blog.models import Category, Series, Tag
from blog.views import ContentActionError, manage_series_posts

from ..auth import BearerAuth, OptionalBearerAuth
from ..common import (
    clamp_page_size,
    post_queryset,
    require_user,
    run_form,
    taxonomy_exists,
    taxonomy_items,
    visible_filter,
)
from ..errors import APIError
from ..schema import (
    CategoryListOut,
    CategoryOut,
    NameIn,
    OkOut,
    PostListOut,
    SeriesIn,
    SeriesListOut,
    SeriesOut,
    SeriesPostIn,
    TagListOut,
    TagOut,
)
from ..serializers import paginate, post_brief

taxonomy_router = Router()


def _series_payload(obj, count=None):
    return {
        "id": obj.pk,
        "name": obj.name,
        "slug": obj.slug,
        "description": obj.description or "",
        "author": obj.author.username,
        "post_count": count if count is not None else 0,
    }


def _items(model_cls, request, author):
    results = taxonomy_items(model_cls, request, author)
    return {"count": len(results), "results": results}


@taxonomy_router.get(
    "/categories", response=CategoryListOut, auth=OptionalBearerAuth(),
    summary="分类列表",
    description="匿名只看到挂有已发布文章的分类；带令牌时额外看到自己尚未使用的分类。",
)
def list_categories(request, author: Optional[str] = None):
    return _items(Category, request, author)


@taxonomy_router.get(
    "/tags", response=TagListOut, auth=OptionalBearerAuth(),
    summary="标签列表",
)
def list_tags(request, author: Optional[str] = None):
    return _items(Tag, request, author)


@taxonomy_router.get(
    "/series", response=SeriesListOut, auth=OptionalBearerAuth(),
    summary="系列列表",
)
def list_series(request, author: Optional[str] = None):
    return _items(Series, request, author)


@taxonomy_router.get(
    "/series/{series_id}", response=SeriesOut, auth=OptionalBearerAuth(),
    summary="系列详情",
)
def series_detail(request, series_id: int):
    obj = taxonomy_exists(Series, series_id, request)
    count = post_queryset().filter(series=obj).filter(visible_filter(request)).distinct().count()
    return _series_payload(obj, count)


@taxonomy_router.get(
    "/series/{series_id}/posts", response=PostListOut, auth=OptionalBearerAuth(),
    summary="系列内文章（按系列序号排序）",
)
def series_posts(request, series_id: int, page: int = 1, page_size: int = 20):
    obj = taxonomy_exists(Series, series_id, request)
    qs = (post_queryset().filter(series=obj).filter(visible_filter(request))
          .order_by("series_order", "created_time").distinct())
    return paginate(request, qs, page, clamp_page_size(page_size),
                    lambda post: post_brief(request, post))


# ── 写：只有创建，且按名称幂等 ──────────────────────────────────────────


def _create_by_name(model_cls, request, name, field_label):
    name = (name or "").strip()
    if not name:
        raise APIError(422, "validation_error", f"{field_label}名不能为空")
    user = require_user(request)
    obj, created = model_cls.get_or_create_for_author(name, user)
    payload = {"id": obj.pk, "name": obj.name, "slug": obj.slug, "post_count": 0}
    if model_cls is Series:
        payload["description"] = obj.description or ""
        payload["author"] = user.username
    return (201 if created else 200), payload


@taxonomy_router.post(
    "/categories", response={200: CategoryOut, 201: CategoryOut}, auth=BearerAuth(),
    summary="新建分类",
    description="与 Web 端 ``/ajax/category/create/`` 同一实现：同名分类复用，不重复创建。",
)
def create_category(request, data: NameIn):
    return _create_by_name(Category, request, data.name, "分类")


@taxonomy_router.post(
    "/tags", response={200: TagOut, 201: TagOut}, auth=BearerAuth(),
    summary="新建标签",
    description="文章端点提交 ``tag_names`` 时会自动建标签，这里只是提前占位。",
)
def create_tag(request, data: NameIn):
    return _create_by_name(Tag, request, data.name, "标签")


@taxonomy_router.post(
    "/series", response={200: SeriesOut, 201: SeriesOut}, auth=BearerAuth(),
    summary="新建系列",
    description="长度等字段规则走 Web 的 ``SeriesForm``；同名系列复用已有条目。",
)
def create_series(request, data: SeriesIn):
    name = (data.name or "").strip()
    if not name:
        raise APIError(422, "validation_error", "系列名不能为空")
    run_form(SeriesForm(data={"name": name, "description": data.description or ""}))
    user = require_user(request)
    series, created = Series.get_or_create_for_author(name, user)
    if created and data.description:
        series.description = data.description
        series.save(update_fields=["description"])
    count = post_queryset().filter(series=series).filter(visible_filter(request)).distinct().count()
    return (201 if created else 200), _series_payload(series, count)


@taxonomy_router.post(
    "/series/{series_id}/posts", response=OkOut, auth=BearerAuth(),
    summary="把文章加入 / 移出系列",
    description="与 Web 端 ``/ajax/series/<id>/manage/`` 相同：不改动文章的修改时间。",
)
def add_post_to_series(request, series_id: int, data: SeriesPostIn):
    user = require_user(request)
    try:
        post = manage_series_posts(user, series_id, data.action, data.post_id)
    except ContentActionError as exc:
        raise APIError(exc.status, exc.code, exc.message) from exc
    verb = "加入系列" if data.action == "add" else "移出系列"
    return {"ok": True, "detail": f"《{post.title}》已{verb}"}
