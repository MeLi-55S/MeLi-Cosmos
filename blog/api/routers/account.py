"""账号端点：签发令牌的登录/注册、当前身份、令牌管理、资料与邀请码。

口令校验、邀请码规则、资料字段全部复用 Django / Web 的既有表单与视图函数
（``UserCreationForm``、``UserProfileForm``、``get_usable_invite``、
``issue_invite_code``），所以移动端不会比浏览器多出任何权限。

除 ``/auth/login`` 与 ``/auth/register`` 外，其余端点用 ``SessionOrBearerAuth``：
App 带 Bearer 令牌，站内页面若改用同一套接口也能带会话 Cookie 工作
（此时非幂等方法必须通过 CSRF 校验，见 ``blog/api/auth.py``）。
"""

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import logout as django_logout
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone
from ninja import Router

from blog.forms import UserProfileForm
from blog.models import ApiToken, InviteCode
from blog.views import (
    _get_client_ip,
    _rate_limit_check,
    consume_invite,
    get_usable_invite,
    issue_invite_code,
)

from ..auth import SessionOrBearerAuth
from ..common import require_user, run_form
from ..errors import APIError, conflict, forbidden, not_found, throttled
from ..schema import (
    AuthenticatedUserOut,
    InviteListOut,
    InviteOut,
    LoginIn,
    LoginOut,
    OkOut,
    ProfileIn,
    ProfileOut,
    RegisterIn,
    TokenCreatedOut,
    TokenListOut,
    TokenNameIn,
)
from ..serializers import invite_out, profile_out, token_out

auth_router = Router()
account_router = Router()

#: 登录/注册限流，防止移动端被用来暴力猜密码
LOGIN_LIMIT = 5
LOGIN_WINDOW = 300
REGISTER_LIMIT = 3
REGISTER_WINDOW = 600

#: 一个账号同时最多持有几个有效令牌
MAX_ACTIVE_TOKENS = 10

INVITE_DAILY_LIMIT = getattr(settings, "INVITE_DAILY_LIMIT", 1)

TOKEN_HINT = ("令牌只显示这一次，请立即保存到安全位置。"
              "撤销：DELETE /api/v1/auth/tokens/{id}，或站内「账号设置 → 访问令牌」。")


def _rate_limit(key, limit, window, message):
    """触到上限时抛 429，窗口剩余时间进 ``Retry-After`` 头。"""
    allowed, retry = _rate_limit_check(key, limit, window)
    if not allowed:
        raise throttled(message, retry_in=max(int(retry or 1), 1))


def _issue_token(user, device_name):
    return ApiToken.issue(user, (device_name or "mobile").strip() or "mobile")


def _login_response(request, user, token, raw):
    return {
        "id": token.pk,
        "name": token.name,
        "prefix": token.prefix,
        "created_at": token.created_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
        "token": raw,
        "hint": TOKEN_HINT,
        "user": profile_out(request, user),
    }


# ── /auth：身份与令牌 ───────────────────────────────────────────────────


@auth_router.post(
    "/login", response=LoginOut,
    summary="用户名 + 密码换取访问令牌",
    description=(
        "不建立浏览器会话：成功只返回一个 PAT，之后所有请求都带 "
        "``Authorization: Bearer mlc_...``。"
        f"连续失败会被限流（{LOGIN_LIMIT} 次 / {LOGIN_WINDOW} 秒）。"
    ),
)
def login(request, data: LoginIn):
    username = (data.username or "").strip()
    if not username or not data.password:
        raise APIError(422, "validation_error", "用户名与密码都不能为空")

    _rate_limit(f"api_login:{username}:{_get_client_ip(request)}",
                LOGIN_LIMIT, LOGIN_WINDOW, "登录尝试过于频繁，请稍后再试。")

    user = authenticate(request, username=username, password=data.password)
    if user is None:
        # authenticate() 对 is_active=False 的账号同样返回 None
        raise APIError(401, "invalid_credentials", "用户名或密码错误")
    if user.profile.is_banned:
        raise forbidden("该账号已被封禁，无法获取访问令牌，请到站内申诉。")

    token, raw = _issue_token(user, data.device_name)
    return _login_response(request, user, token, raw)


@auth_router.post(
    "/register", response={201: LoginOut},
    summary="邀请码注册并换取令牌",
    description="站内注册一直要求邀请码，API 同样如此。字段规则复用 Django 的 "
                "``UserCreationForm``（长度、两次密码一致、弱密码校验）。",
)
def register(request, data: RegisterIn):
    _rate_limit(f"api_register:{_get_client_ip(request)}",
                REGISTER_LIMIT, REGISTER_WINDOW, "注册尝试过于频繁，请稍后再试。")

    invite = get_usable_invite((data.code or "").strip())
    form = run_form(UserCreationForm(data={
        "username": data.username,
        "password1": data.password1,
        "password2": data.password2,
    }), "注册信息不合法")
    user = form.save()

    if not consume_invite(invite, user):
        user.delete()
        raise conflict("邀请码已被使用")

    token, raw = _issue_token(user, data.device_name)
    return 201, _login_response(request, user, token, raw)


@auth_router.get(
    "/me", response=AuthenticatedUserOut, auth=SessionOrBearerAuth(),
    summary="当前身份",
    description="带令牌时 ``token`` 字段就是本次请求使用的令牌（含最后使用时间）。",
)
def me(request):
    user = require_user(request)
    profile = profile_out(request, user)
    token = getattr(request, "api_token", None)
    return {
        "username": user.username,
        "display_name": profile.display_name,
        "is_staff": user.is_staff,
        "avatar_url": profile.avatar_url,
        "url": profile.url,
        "token": token_out(token) if token is not None else None,
    }


@auth_router.post(
    "/logout", response=OkOut, auth=SessionOrBearerAuth(),
    summary="退出登录",
    description="带令牌时撤销当前令牌（其他令牌不受影响）；带会话时清除会话。",
)
def logout(request):
    require_user(request)
    token = getattr(request, "api_token", None)
    if token is not None:
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        return {"ok": True, "detail": f"令牌 {token.prefix}… 已撤销"}
    django_logout(request)
    return {"ok": True, "detail": "会话已退出"}


@auth_router.get(
    "/tokens", response=TokenListOut, auth=SessionOrBearerAuth(),
    summary="我的令牌列表",
    description="只返回令牌元信息（名称、前缀 12 位、使用时间），永不返回明文或哈希。",
)
def list_tokens(request):
    user = require_user(request)
    items = [token_out(t) for t in ApiToken.objects.filter(user=user)]
    return {"count": len(items), "results": items}


@auth_router.post(
    "/tokens", response={201: TokenCreatedOut}, auth=SessionOrBearerAuth(),
    summary="新建令牌",
    description=f"每个账号最多 {MAX_ACTIVE_TOKENS} 个有效令牌。明文只在这次响应里出现一次。",
)
def create_token(request, data: TokenNameIn):
    user = require_user(request)
    if ApiToken.objects.filter(user=user, revoked_at=None).count() >= MAX_ACTIVE_TOKENS:
        raise conflict(f"有效令牌已达上限（{MAX_ACTIVE_TOKENS} 个），请先撤销不再使用的令牌")
    token, raw = _issue_token(user, data.name)
    payload = token_out(token).model_dump()
    payload.update({"token": raw, "hint": TOKEN_HINT})
    return 201, payload


@auth_router.delete(
    "/tokens/{token_id}", response=OkOut, auth=SessionOrBearerAuth(),
    summary="撤销令牌",
    description="软撤销：保留记录用于审计，令牌立即失效。允许撤销当前正在使用的令牌。",
)
def revoke_token(request, token_id: int):
    user = require_user(request)
    token = ApiToken.objects.filter(pk=token_id, user=user).first()
    if token is None:
        raise not_found("令牌不存在")
    if token.revoked_at is None:
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
    return {"ok": True, "detail": f"令牌 {token.prefix}… 已撤销"}


# ── /account：资料与邀请码 ──────────────────────────────────────────────


@account_router.get(
    "/profile", response=ProfileOut, auth=SessionOrBearerAuth(),
    summary="我的资料",
)
def get_profile(request):
    return profile_out(request, require_user(request))


def _profile_payload(user, data: ProfileIn):
    """ProfileIn → UserProfileForm 字典（未提交的字段沿用当前值）。"""
    profile = user.profile
    base = {
        "display_name": profile.display_name or "",
        "title": profile.title or "",
        "bio": profile.bio or "",
        "website": profile.website or "",
        "github": profile.github_username or profile.github or "",
        "email": user.email or "",
    }
    base.update({key: value for key, value in data.model_dump(exclude_unset=True).items()
                 if key in base and value is not None})
    return base


@account_router.patch(
    "/profile", response=ProfileOut, auth=SessionOrBearerAuth(),
    summary="修改我的资料",
    description="字段规则与站内「编辑资料」页完全相同（含 GitHub 用户名自动补全为 URL）。"
                "未提交的字段保持不变；Mastodon 与头像不在本端点范围内。",
)
def update_profile(request, data: ProfileIn):
    user = require_user(request)
    form = run_form(
        UserProfileForm(data=_profile_payload(user, data), instance=user.profile),
        "资料填写有误",
    )
    form.save()
    return profile_out(request, user)


@account_router.get(
    "/invites", response=InviteListOut, auth=SessionOrBearerAuth(),
    summary="我发出的邀请码",
)
def list_invites(request):
    user = require_user(request)
    codes = InviteCode.objects.filter(inviter=user).select_related("invitee") \
        .order_by("-created_at")
    items = [invite_out(request, code) for code in codes]
    return {"count": len(items), "results": items}


@account_router.post(
    "/invites", response={201: InviteOut}, auth=SessionOrBearerAuth(),
    summary="生成邀请码",
    description=f"频次限制与站内一致：非管理员每个自然日最多 {INVITE_DAILY_LIMIT} 个未使用的邀请码，"
                "超限返回 429。",
)
def create_invite(request):
    user = require_user(request)
    code = issue_invite_code(user)   # 超限抛 ContentActionError → 统一转 429
    return 201, invite_out(request, code)
