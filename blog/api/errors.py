"""统一的 API 异常与错误响应格式。

所有错误都返回同一形状，便于移动端解析::

    {"error": {"code": "not_found", "message": "文章不存在"}}

``APIError`` 继承 django-ninja 的 ``HttpError``（1.7 里没有 ``ApiError`` 这个名字），
因此未注册自定义处理器的路由也会按 ``status_code`` 返回，而不是掉进 500。

``blog.views.ContentActionError`` 是 Web 与 API 共用的"写操作失败"异常，在这里
一并翻译：各 router 直接调用 views 里的那份实现即可，不必逐处 try/except。

连"路径拼错、方法用错"也走同一形状：``ApiUrlErrorShapeMiddleware`` 挂在
``MIDDLEWARE`` 末尾，把 ``/api/`` 下 URL 解析阶段产生的 HTML 404 / 405 换成本文件的
JSON 契约。这类响应不经过 ninja 的 exception handler——它们是正常返回的响应，
不是抛出的异常。
"""

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from ninja.errors import HttpError, ValidationError

from blog.views import ContentActionError

logger = logging.getLogger("django.request")


class APIError(HttpError):
    """带机器可读 ``code`` 的业务异常。"""

    def __init__(self, status_code, code, message, retry_after=None):
        super().__init__(status_code=status_code, message=message)
        self.code = code
        self.retry_after = retry_after


def unauthorized(message="未认证或令牌无效"):
    return APIError(401, "unauthorized", message)


def forbidden(message="没有权限"):
    return APIError(403, "forbidden", message)


def not_found(message="资源不存在"):
    return APIError(404, "not_found", message)


def conflict(message="资源冲突"):
    return APIError(409, "conflict", message)


def throttled(message="请求过于频繁", retry_in=60):
    return APIError(429, "throttled", message, retry_after=retry_in)


# ninja 内置异常 → 我们的机器可读 code
_BUILTIN_CODES = {
    "AuthenticationError": "unauthorized",
    "AuthorizationError": "forbidden",
    "Throttled": "throttled",
}


def _validation_details(exc):
    """把 ninja 的 ValidationError.errors（list[dict]）压成 {字段: 消息} 便于展示。"""
    details = {}
    for err in getattr(exc, "errors", None) or []:
        loc = ".".join(str(part) for part in err.get("loc", ()) if part != "body") or "_"
        details.setdefault(loc, []).append(err.get("msg", "参数不合法"))
    return details


def error_body_handler(request, exc):
    """把业务异常 / 校验错误 / Django 的 404、403 统一成同一种 JSON。"""
    details = None

    if isinstance(exc, APIError):
        code, message, status = exc.code, exc.message, exc.status_code
        details = getattr(exc, "details", None)
    elif isinstance(exc, ContentActionError):
        code, message, status = exc.code, exc.message, exc.status
    elif isinstance(exc, ValidationError):
        code, message, status = "validation_error", "请求参数不合法", 422
        details = _validation_details(exc)
        flat = [m for msgs in details.values() for m in msgs]
        if flat:
            message = flat[0]
    elif isinstance(exc, HttpError):
        code = _BUILTIN_CODES.get(type(exc).__name__, "http_error")
        message, status = exc.message, exc.status_code
    elif isinstance(exc, Http404):
        code, message, status = "not_found", "资源不存在", 404
    elif isinstance(exc, PermissionDenied):
        code, message, status = "forbidden", "没有权限", 403
    else:  # pragma: no cover - 兜底
        logger.exception("Unhandled API error: %s %s", request.method, request.path)
        if settings.DEBUG:
            raise exc  # 本地开发时保留真实 traceback
        code, message, status = "server_error", "服务器内部错误", 500

    body = {"error": {"code": code, "message": str(message)}}
    if details:
        body["details"] = details

    response = JsonResponse(body, status=status)
    retry_after = getattr(exc, "retry_after", None) or getattr(exc, "wait", None)
    if retry_after:
        response["Retry-After"] = str(int(retry_after))
    return response


class ApiUrlErrorShapeMiddleware:
    """把 ``/api/`` 下"没落到业务代码里"的 404 / 405 也换成统一 JSON。

    ninja 的 exception handler 只处理视图内部抛出的异常，而这两类响应是正常返回
    的对象：拼错路径 → Django 解析失败的 HTML 404 页；方法不对 → ``PathView`` 的
    ``HttpResponseNotAllowed``。移动端只认 ``error.code``，拿到 HTML 就必须整段读
    body 才能分支，所以在这里补齐契约。已经是 JSON 的一律放行（业务 404 就走这条）。
    """

    #: 只有 API 前缀下的响应会被改写；站内页面维持 HTML 404 页不变
    prefix = "/api/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith(self.prefix):
            return response
        if response.status_code not in (404, 405):
            return response
        if response.headers.get("Content-Type", "").startswith("application/json"):
            return response

        allow = response.headers.get("Allow", "")
        if response.status_code == 405:
            code = "method_not_allowed"
            message = f"{request.method} 不被 {request.path} 支持"
            if allow:
                message += f"（可用：{allow}）"
        else:
            code, message = "not_found", f"端点不存在：{request.method} {request.path}"

        new = JsonResponse({"error": {"code": code, "message": message}},
                           status=response.status_code)
        if allow:
            new["Allow"] = allow
        return new
