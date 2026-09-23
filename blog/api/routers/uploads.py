"""上传端点：文章配图与头像。

处理管线（大小上限、MD5 去重、去 EXIF、缩放、转 WebP、落库）与站内 AJAX 完全同源：
``blog/views.py`` 的 ``store_uploaded_image`` / ``store_avatar``，两端只有一层
"把 ``ContentActionError`` 转成什么形态" 的差异（这里由统一异常处理器转成 JSON）。

请求格式：``multipart/form-data``，文件字段名 ``image``。
"""

from ninja import File, Router, UploadedFile

from blog.models import UploadedImage
from blog.views import store_avatar, store_uploaded_image

from ..auth import SessionOrBearerAuth
from ..common import clamp_page_size, require_user
from ..schema import AvatarOut, UploadListOut, UploadOut
from ..serializers import paginate, upload_item

uploads_router = Router()


@uploads_router.post(
    "/image", response=UploadOut, auth=SessionOrBearerAuth(),
    summary="上传文章配图",
    description=(
        "返回可直接写进 Markdown 的 URL。相同内容（MD5 相同）不会重复入库，"
        "响应里的 ``dedup=true`` 表示复用了已有文件。上限 10MB，统一转成 WebP。"
    ),
)
def upload_image(request, image: UploadedFile = File(..., description="图片文件，字段名 image")):
    user = require_user(request)
    return store_uploaded_image(user, image)


@uploads_router.get(
    "/images", response=UploadListOut, auth=SessionOrBearerAuth(),
    summary="我上传过的图片",
)
def list_images(request, page: int = 1, page_size: int = 20):
    user = require_user(request)
    qs = UploadedImage.objects.filter(uploader=user).order_by("-created_time")
    return paginate(request, qs, page, clamp_page_size(page_size), upload_item)


@uploads_router.post(
    "/avatar", response=AvatarOut, auth=SessionOrBearerAuth(),
    summary="更换头像",
    description="与站内裁剪上传同一套处理：旧头像文件会被删除，新头像固定为 "
                "``avatars/<用户名>.webp``。",
)
def upload_avatar(request, image: UploadedFile = File(..., description="头像图片")):
    user = require_user(request)
    return store_avatar(user, image)
