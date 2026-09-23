"""评论与点赞端点。

写逻辑一律调用 ``blog/views.py`` 里的那一份实现（``create_comment`` / ``toggle_like``），
所以限流、游客首评过审、重复提交拦截、通知作者、点赞文案这些规则不会因为换了
客户端就被绕过。

游客评论也走这里：认证依赖用 ``OptionalBearerAuth``——带有效令牌时按登录用户处理，
不带则落到游客分支（昵称 + 邮箱必填，首评进入待审）。
"""

from typing import Optional

from django.db.models import Q
from ninja import Router

from blog.forms import CommentForm
from blog.models import Comment
from blog.views import (
    ContentActionError,
    create_comment,
    get_like_state,
    resolve_content_target,
    toggle_like,
)

from ..auth import BearerAuth, OptionalBearerAuth
from ..common import clamp_page_size, current_user, require_user, run_form
from ..errors import APIError, forbidden, not_found
from ..schema import (
    CommentIn,
    CommentListOut,
    CommentOut,
    LikeIn,
    LikeOut,
    LikeStateMapOut,
)
from ..serializers import comment_out, paginate

social_router = Router()


def _visible_comments(request):
    """当前请求能看到哪些评论。

    * 管理员：全部（含未过审），便于在移动端审核
    * 登录用户：已过审的 + 自己发的（自己的待审条目带 ``is_pending``）
    * 匿名：只有已过审的
    """
    user = current_user(request)
    if user is None:
        return Comment.objects.filter(is_visible=True)
    if user.is_staff:
        return Comment.objects.all()
    return Comment.objects.filter(Q(is_visible=True) | Q(user=user))


@social_router.get(
    "/comments", response=CommentListOut, auth=OptionalBearerAuth(),
    summary="评论列表",
    description="给定 ``content_type`` + ``object_id`` 列某篇内容的评论；不带参数则列全站最新评论。"
                "匿名只看到已过审的评论，登录用户额外看到自己待审的那些（``is_pending=true``）。",
)
def list_comments(
    request,
    content_type: Optional[str] = None,
    object_id: Optional[int] = None,
    mine: bool = False,
    page: int = 1,
    page_size: int = 20,
):
    qs = _visible_comments(request).select_related(
        "user__profile", "content_type"
    ).order_by("-created_time")

    if mine:
        user = require_user(request, "mine=true 需要携带令牌")
        qs = qs.filter(user=user)
    if content_type or object_id:
        ct, _obj = resolve_content_target(content_type, object_id, current_user(request))
        qs = qs.filter(content_type=ct, object_id=_obj.pk)

    return paginate(request, qs, page, clamp_page_size(page_size),
                    lambda comment: comment_out(request, comment))


@social_router.post(
    "/comments", response={201: CommentOut, 202: CommentOut}, auth=OptionalBearerAuth(),
    summary="发表评论",
    description=(
        "登录用户直接可见（201）；游客的首条评论要过审，返回 202 与 ``is_pending=true``，"
        "同一邮箱有过审历史后自动直发。字段规则、限流与蜜罐检测与 Web 完全一致。"
    ),
)
def create_comment_endpoint(request, data: CommentIn):
    user = current_user(request)
    is_guest = user is None

    if data.website:
        # 蜜罐：与 Web 一样静默拒绝，但给移动端一个明确状态码。
        # 放在表单校验之前，免得机器人从报错里学到"补个昵称就能过"。
        raise APIError(422, "spam_detected", "评论提交失败，请重试。")

    form = run_form(
        CommentForm(
            data={"content": data.content,
                  "guest_name": data.guest_name or "",
                  "guest_email": data.guest_email or "",
                  "website": ""},
            is_guest=is_guest,
        ),
        "评论参数不合法",
    )

    comment = create_comment(
        request, data.content_type, data.object_id,
        form.cleaned_data["content"],
        guest_name=form.cleaned_data.get("guest_name", ""),
        guest_email=form.cleaned_data.get("guest_email", ""),
    )
    status = 201 if comment.is_visible else 202
    return status, comment_out(request, comment)


@social_router.get(
    "/comments/{comment_id}", response=CommentOut, auth=OptionalBearerAuth(),
    summary="评论详情",
)
def get_comment(request, comment_id: int):
    comment = (Comment.objects.select_related("user__profile", "content_type")
               .filter(pk=comment_id).first())
    if comment is None:
        raise not_found("评论不存在")
    user = current_user(request)
    if not comment.is_visible and not (
        user is not None and (user.is_staff or (comment.user_id == user.pk))
    ):
        raise not_found("评论不存在")
    return comment_out(request, comment)


@social_router.post(
    "/comments/{comment_id}/visibility", response=CommentOut, auth=BearerAuth(),
    summary="上/下架评论（仅管理员）",
    description="与 Web 管理界面的审核开关同一动作：切换 ``is_visible``。",
)
def toggle_comment_visibility(request, comment_id: int):
    user = require_user(request)
    if not user.is_staff:
        raise forbidden("只有管理员可以审核评论")
    comment = Comment.objects.filter(pk=comment_id).first()
    if comment is None:
        raise not_found("评论不存在")
    comment.is_visible = not comment.is_visible
    comment.save(update_fields=["is_visible", "modified_time"])
    return comment_out(request, comment)


# ── 点赞 ────────────────────────────────────────────────────────────────


@social_router.get(
    "/likes", response=LikeStateMapOut, auth=OptionalBearerAuth(),
    summary="批量查询点赞状态",
    description="``targets=blog.post:1,blog.memo:2``，一次拿回列表页所有卡片的点赞数；"
                "未过审或无权访问的内容不会出现在结果里。",
)
def like_states(request, targets: str = ""):
    states = {}
    user = current_user(request)
    for raw in (targets or "").split(","):
        key = raw.strip()
        if not key or ":" not in key:
            continue
        ct_key, _, pk = key.partition(":")
        try:
            obj_id = int(pk)
        except ValueError:
            continue
        try:
            ct, _obj = resolve_content_target(ct_key, obj_id, user, verb="查看")
        except ContentActionError:
            continue  # 批量查询里跳过无效/无权项，不整体报错
        state = get_like_state(ct, obj_id, user)
        states[f"{ct.app_label}.{ct.model}:{obj_id}"] = {
            "count": state["count"],
            "display_text": state["display_text"],
            "user_liked": state["user_liked"],
        }
    return {"states": states}


@social_router.post(
    "/likes", response=LikeOut, auth=BearerAuth(),
    summary="点赞 / 取消点赞",
    description="开关语义与 Web 的心形按钮一致：已赞则取消，未赞则点赞并通知作者。",
)
def like_toggle(request, data: LikeIn):
    return toggle_like(request, data.content_type, data.object_id)
