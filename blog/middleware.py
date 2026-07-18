from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class BanCheckMiddleware:
    """Log out banned users on every request, except ban-appeal page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            allowed_paths = [
                reverse('ban_appeal'),
                reverse('logout'),
                reverse('login'),
            ]
            if request.path not in allowed_paths and not request.path.startswith('/admin/'):
                try:
                    profile = request.user.profile
                except Exception:
                    return self.get_response(request)

                if profile.is_banned:
                    reason = profile.banned_reason or '账号已被封禁'
                    if profile.is_permanent_ban:
                        reason = '[永久封禁] ' + reason
                    messages.error(request, f'您的账号已被封禁。原因：{reason}')
                    logout(request)
                    return redirect(reverse('login'))

        return self.get_response(request)
