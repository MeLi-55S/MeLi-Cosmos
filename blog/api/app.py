"""``/api/v1`` 的装配入口（django-ninja ``NinjaAPI``）。

- 文档：``/api/v1/docs``（Swagger UI）、规范：``/api/v1/openapi.json``
- 错误：所有异常（业务 / 校验 / 404 / 403 / 限流 / Web 共用的 ``ContentActionError``）
  都由 ``blog.api.errors.error_body_handler`` 归一成 ``{"error": {"code", "message"}}``
- CSRF：django-ninja 在中间件层对整个 API 豁免 CSRF（它假定鉴权不走 Cookie）。
  本层的写端点用 ``Authorization: Bearer``；唯一接受会话 Cookie 的
  ``SessionOrBearerAuth`` 自行补做了 CSRF 校验，见 ``blog/api/auth.py``
- 认证依赖分三档：
  * ``OptionalBearerAuth`` —— 公开读端点与游客评论，带令牌可解锁本人的私有内容
  * ``BearerAuth``         —— 内容写作类写端点（文章 / 碎碎念 / 分类法 / 点赞）
  * ``SessionOrBearerAuth``—— 账号面（令牌、资料、邀请码、上传、收件箱），
    App 用令牌、站内页面可用会话 Cookie
- URL namespace 为 ``api-v1``，移动端不要依赖反向解析，直接用返回体里的绝对 URL

新增一组端点时：在 ``blog/api/routers/`` 下建模块，把 Router 加到下面的
``_ROUTES`` 列表即可，不要在本文件里写业务逻辑。
"""

from django.core.exceptions import PermissionDenied
from django.http import Http404
from ninja import NinjaAPI
from ninja.errors import HttpError, ValidationError

from blog.views import ContentActionError

from .errors import APIError, error_body_handler
from .routers.account import account_router, auth_router
from .routers.content import memos_router, posts_router
from .routers.discovery import discovery_router
from .routers.inbox import inbox_router
from .routers.meta import API_VERSION, meta_router
from .routers.social import social_router
from .routers.taxonomy import taxonomy_router
from .routers.uploads import uploads_router

DESCRIPTION = """\
MeLi Cosmos 的公开 REST API，供移动端与第三方客户端使用。

**认证**：站内「账号设置 → 访问令牌」或 `POST /api/v1/auth/login` 签发 `mlc_` 开头的
个人访问令牌（PAT），后续请求带上 `Authorization: Bearer mlc_xxx`。明文令牌只在签发时
返回一次，库里只存 SHA-256 哈希。

**可见性**：匿名只读已发布内容；带作者本人的令牌可读写其草稿与私密内容。

**错误格式**：所有非 2xx 响应体形如 `{"error": {"code": "not_found", "message": "…"}}`，
参数错误额外带 `details`；限流响应带 `Retry-After` 头。`/api/v1/` 下拼错路径（`not_found`）
或用错方法（`method_not_allowed`，可用方法同时出现在 `Allow` 头与文案里）也返回这个形状，
不会掉进站内的 HTML 404 页。

**与 Web 同源**：Markdown 渲染、点赞文案、评论审核与限流、图片处理、邀请码规则都由
`blog/views.py` 与 `blog/forms.py` 的既有实现提供，移动端拿不到任何 Web 端没有的权限。
"""

api = NinjaAPI(
    title="MeLi Cosmos API",
    version=API_VERSION,
    description=DESCRIPTION,
    openapi_url="/openapi.json",
    docs_url="/docs",
)

# 顺序无关：_lookup_exception_handler 按 MRO 取最具体的注册项
for _exc in (APIError, ContentActionError, HttpError, ValidationError,
             Http404, PermissionDenied, Exception):
    api.add_exception_handler(_exc, error_body_handler)

_ROUTES = [
    ("", meta_router, ["站点"]),
    ("/posts", posts_router, ["文章"]),
    ("/memos", memos_router, ["碎碎念"]),
    ("", taxonomy_router, ["分类法"]),
    ("", discovery_router, ["发现"]),
    ("", social_router, ["评论与点赞"]),
    ("/inbox", inbox_router, ["收件箱"]),
    ("/auth", auth_router, ["认证与令牌"]),
    ("/account", account_router, ["账号"]),
    ("/uploads", uploads_router, ["上传"]),
]

for prefix, router, tags in _ROUTES:
    api.add_router(prefix, router, tags=tags)
