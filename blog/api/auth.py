"""PAT（个人访问令牌）认证。

- 令牌格式：``mlc_`` + 40 位十六进制，只在签发时明文出现一次，库里存 SHA-256。
- 传输方式：``Authorization: Bearer mlc_xxx``。
- 三种依赖：
  * ``BearerAuth``          —— 必须带有效令牌（写端点用）
  * ``OptionalBearerAuth``  —— 带则解析、不带则匿名（读端点用；带了但无效会 401，不静默降级）
  * ``SessionOrBearerAuth`` —— 浏览器 session 或令牌任一即可（Web 设置页与 App 共用）

通过后 ``request.user`` 是令牌所属用户，``request.api_token`` 是令牌对象本身，
``request.auth`` 由 django-ninja 自动写入（同样指向令牌）。

被封禁的账号（``UserProfile.is_banned`` 或 ``User.is_active=False``）一律 403，
与 ``blog.middleware.BanCheckMiddleware`` 对 Web 端"直接踢下线"的语义保持一致。

CSRF：Bearer 令牌不依赖 Cookie，天然免疫；唯一接受会话 Cookie 的
``SessionOrBearerAuth`` 自己补了一次 CSRF 校验——因为 django-ninja 会在 Django
中间件层对整个 API 豁免 CSRF（见 ``ninja.operation.PathView.get_view``）。
"""

from django.http import HttpRequest
from ninja.security import HttpBearer
from ninja.security.base import AuthBase
from ninja.utils import check_csrf

from blog.models import ApiToken

from .errors import APIError, forbidden, unauthorized

_BAN_MESSAGE = "该账号已被封禁，如需申诉请访问 /accounts/appeal/"
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")


def authenticate_bearer(request, raw_token):
    """校验令牌并把用户挂到 request 上；失败抛 APIError。"""
    if not raw_token:
        raise unauthorized("缺少 Bearer 令牌")

    api_token = (
        ApiToken.objects.select_related("user", "user__profile")
        .filter(token_hash=ApiToken.hash_token(raw_token))
        .first()
    )
    if api_token is None or not api_token.is_active:
        raise unauthorized("令牌无效或已撤销")
    user = api_token.user
    if not user.is_active:
        raise forbidden(_BAN_MESSAGE)
    profile = getattr(user, "profile", None)
    if profile is not None and profile.is_banned:
        raise forbidden(_BAN_MESSAGE)

    api_token.touch()
    request.user = user
    request.api_token = api_token
    return api_token


def extract_header_token(request):
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    return None


class BearerAuth(HttpBearer):
    """要求有效令牌；``request.auth`` / ``request.api_token`` 均为令牌对象。"""

    def authenticate(self, request, token):
        return authenticate_bearer(request, token)


class OptionalBearerAuth(AuthBase):
    """可选令牌：匿名可读，带令牌时 request.user 会被换成令牌主人。"""

    openapi_type: str = "http"
    openapi_scheme: str = "bearer"
    openapi_bearerFormat: str = "opaque"
    openapi_description: str = (
        "可选。省略时为匿名访问；带上作者本人的令牌可读到草稿与私密内容"
    )

    def __call__(self, request: HttpRequest):
        request.api_token = None
        token = extract_header_token(request)
        if token is not None:
            authenticate_bearer(request, token)
        return True


class SessionOrBearerAuth(AuthBase):
    """浏览器 session 或 Bearer 令牌皆可（Web 设置页与 App 共用同一端点）。

    走会话 Cookie 的非幂等方法必须带 CSRF 头，否则 Web 端点可被跨站伪造。
    """

    openapi_type: str = "http"
    openapi_scheme: str = "bearer"
    openapi_bearerFormat: str = "opaque"
    openapi_description: str = (
        "Bearer mlc_xxx；浏览器访问时也可用已登录的会话 Cookie（需带 X-CSRFToken）"
    )

    def __call__(self, request: HttpRequest):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            if request.method not in _SAFE_METHODS and check_csrf(request) is not None:
                raise APIError(
                    403, "csrf_failed",
                    "会话请求缺少有效的 CSRF 令牌，请改用 Bearer 令牌或带上 X-CSRFToken",
                )
            request.api_token = None
            return True
        token = extract_header_token(request)
        if token is not None:
            authenticate_bearer(request, token)
            return True
        raise unauthorized("需要登录：提供 Bearer 令牌或使用浏览器会话")
