"""收件箱（通知）端点，全部需要登录。

移动端需要两件事：拉取通知列表（含未读数做角标）、标记已读。
写入通知的时机在 Web 与 API 完全共用同一处实现——见 ``blog/views.py`` 的
``create_comment`` / ``toggle_like``，移动端不会产生额外类型的通知。
"""

from typing import Optional

from ninja import Router

from blog.models import Notification
from blog.views import mark_notifications_read

from ..auth import SessionOrBearerAuth
from ..common import clamp_page_size, require_user
from ..errors import APIError
from ..schema import MarkReadIn, MarkReadOut, NotificationListOut, UnreadOut
from ..serializers import notification_out, paginate

inbox_router = Router()


def _unread_count(user):
    return Notification.objects.filter(recipient=user, is_read=False).count()


@inbox_router.get(
    "", response=NotificationListOut, auth=SessionOrBearerAuth(),
    summary="通知列表",
    description="``unread_only=true`` 只看未读；``type`` 可按 comment / reply / like / system 过滤。",
)
def list_notifications(request, unread_only: bool = False, type: Optional[str] = None,
                       page: int = 1, page_size: int = 20):
    user = require_user(request)
    qs = Notification.objects.filter(recipient=user).select_related(
        "actor__profile", "content_type"
    ).order_by("-created_time")
    if unread_only:
        qs = qs.filter(is_read=False)
    if type:
        qs = qs.filter(notification_type=type)
    result = paginate(request, qs, page, clamp_page_size(page_size),
                      lambda obj: notification_out(request, obj))
    result["unread"] = _unread_count(user)
    return result


@inbox_router.get(
    "/unread", response=UnreadOut, auth=SessionOrBearerAuth(),
    summary="未读数量（角标）",
)
def unread_count(request):
    user = require_user(request)
    return {"unread": _unread_count(user)}


@inbox_router.post(
    "/read", response=MarkReadOut, auth=SessionOrBearerAuth(),
    summary="标记已读",
    description="``mark_all=true`` 全部已读；否则按 ``ids`` 逐条标记（不属于你的通知会被忽略）。",
)
def mark_read(request, data: MarkReadIn):
    user = require_user(request)
    if not data.mark_all and not data.ids:
        raise APIError(422, "validation_error", "请提供 ids，或设置 mark_all=true")
    updated = mark_notifications_read(
        user, pks=data.ids or [], all_unread=data.mark_all,
    )
    return {"updated": updated, "unread": _unread_count(user)}
