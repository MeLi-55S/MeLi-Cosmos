"""
MeLi Cosmos v3.0 - Admin Configuration (multi-user).
"""
from django.contrib import admin, messages
from django.utils import timezone
from django.contrib.auth.models import User

from .models import Category, Tag, Post, Memo, Series, UserProfile, InviteCode, ViewLog, Like, Comment, BanAppeal


def _get_invitee_ids(user):
    """Return set of user IDs invited by `user` (used + has invitee)."""
    return set(InviteCode.objects.filter(
        inviter=user, is_used=True
    ).exclude(invitee=None).values_list('invitee_id', flat=True))


def _ban_chain(user, banned_by, reason, is_permanent=False):
    """Recursively ban `user` and all users in their invite chain."""
    profile = user.profile
    if profile.is_permanent_ban:
        return
    was_already = profile.is_banned
    profile.is_banned = True
    if is_permanent:
        profile.is_permanent_ban = True
    if not was_already:
        profile.banned_by = banned_by
        profile.banned_reason = reason or ''
        profile.banned_at = timezone.now()
        profile.save(update_fields=['is_banned', 'is_permanent_ban', 'banned_by', 'banned_reason', 'banned_at'])
    elif is_permanent and not profile.is_permanent_ban:
        profile.is_permanent_ban = True
        profile.save(update_fields=['is_permanent_ban'])

    for uid in _get_invitee_ids(user):
        try:
            invitee = User.objects.get(pk=uid)
        except User.DoesNotExist:
            continue
        chain_reason = f'上级用户 {user.username} 被封禁，连带封禁。原始原因：{reason or "未提供"}'
        _ban_chain(invitee, banned_by, chain_reason, is_permanent=False)


def _unban_chain(user):
    """Recursively unban `user` and all users in their invite chain (unless permanent)."""
    profile = user.profile
    if profile.is_permanent_ban:
        return
    if profile.is_banned:
        profile.is_banned = False
        profile.banned_by = None
        profile.banned_reason = ''
        profile.banned_at = None
        profile.save(update_fields=['is_banned', 'banned_by', 'banned_reason', 'banned_at'])

    for uid in _get_invitee_ids(user):
        try:
            invitee = User.objects.get(pk=uid)
        except User.DoesNotExist:
            continue
        _unban_chain(invitee)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "author"]
    search_fields = ["name"]
    list_filter = ["author"]

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ["author"]
        return []


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "author"]
    search_fields = ["name"]
    list_filter = ["author"]


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "author",
        "category",
        "series",
        "status",
        "views",
        "created_time",
        "unique_id_short",
    ]
    list_filter = ["status", "category", "tags", "series", "author", "created_time"]
    search_fields = ["title", "body", "unique_id"]
    filter_horizontal = ["tags"]
    date_hierarchy = "created_time"
    readonly_fields = ["unique_id", "views", "created_time", "modified_time"]
    fieldsets = (
        ("核心信息", {
            "fields": ("title", "slug", "author", "unique_id")
        }),
        ("内容", {
            "fields": ("cover", "body", "excerpt")
        }),
        ("分类与标签", {
            "fields": ("category", "tags", "series", "series_order")
        }),
        ("状态与统计", {
            "fields": ("status", "views", "created_time", "modified_time")
        }),
    )

    @admin.display(description="UUID(短)")
    def unique_id_short(self, obj):
        return str(obj.unique_id)[:12] + "..."


@admin.register(Series)
class SeriesAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "author", "description"]
    search_fields = ["name"]
    list_filter = ["author"]


@admin.register(Memo)
class MemoAdmin(admin.ModelAdmin):
    list_display = ["content_short", "author", "is_public", "created_time"]
    list_filter = ["is_public", "author", "created_time"]
    search_fields = ["content"]

    @admin.display(description="内容")
    def content_short(self, obj):
        return obj.content[:30]


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "display_name", "ban_status", "is_permanent_ban", "banned_by_short", "banned_at"]
    search_fields = ["user__username", "display_name"]
    list_filter = ["is_banned", "is_permanent_ban"]
    readonly_fields = ["banned_by", "banned_at"]

    fieldsets = (
        ("基本信息", {
            "fields": ("user", "display_name", "title", "bio")
        }),
        ("头像", {
            "fields": ("avatar", "avatar_url")
        }),
        ("社交链接", {
            "fields": ("website", "github")
        }),
        ("封禁管理", {
            "fields": ("is_banned", "is_permanent_ban", "banned_reason", "banned_by", "banned_at"),
            "description": "勾选「是否封禁」保存后将递归封禁该用户邀请链上的所有后继用户。取消勾选则递归解封（永久封禁除外）。"
        }),
    )

    @admin.display(description='封禁状态')
    def ban_status(self, obj):
        if obj.is_permanent_ban:
            return '⛔ 永久封禁'
        if obj.is_banned:
            return '🚫 已封禁'
        return '✅ 正常'

    @admin.display(description='执行人')
    def banned_by_short(self, obj):
        if obj.banned_by:
            return obj.banned_by.username
        return '-'

    def save_model(self, request, obj, form, change):
        if not change:
            super().save_model(request, obj, form, change)
            return

        old = UserProfile.objects.filter(pk=obj.pk).only('is_banned', 'is_permanent_ban').first()
        was_banned = old.is_banned if old else False
        was_permanent = old.is_permanent_ban if old else False

        super().save_model(request, obj, form, change)
        obj.refresh_from_db()

        # Just banned: recursively ban invite chain
        if obj.is_banned and not was_banned:
            reason = obj.banned_reason or f'管理员 {request.user.username} 封禁'
            _ban_chain(obj.user, request.user, reason, is_permanent=obj.is_permanent_ban)
            count = InviteCode.objects.filter(inviter=obj.user, is_used=True).exclude(invitee=None).count()
            messages.warning(request,
                f'已封禁 {obj.user.username} 及其邀请链上的所有后继用户。'
                f'该用户直接邀请了 {count} 人。'
            )

        # Just unbanned: recursively unban invite chain
        if not obj.is_banned and was_banned:
            _unban_chain(obj.user)
            messages.success(request, f'已解封 {obj.user.username} 及其邀请链上的所有后继用户（永久封禁除外）。')

        # Permanent flag just set on already-banned user
        if obj.is_permanent_ban and not was_permanent and obj.is_banned:
            # Re-ban chain with permanent flag (won't affect already-banned, just sets flag on chain)
            for uid in _get_invitee_ids(obj.user):
                try:
                    invitee = User.objects.get(pk=uid)
                except User.DoesNotExist:
                    continue
                p = invitee.profile
                if p.is_banned and not p.is_permanent_ban:
                    p.is_permanent_ban = True
                    p.save(update_fields=['is_permanent_ban'])
            messages.warning(request, f'已将 {obj.user.username} 及其已被封禁的后继设为永久封禁。')


@admin.register(InviteCode)
class InviteCodeAdmin(admin.ModelAdmin):
    list_display = ["code_short", "inviter", "invitee", "is_used", "created_at", "expires_at"]
    list_filter = ["is_used", "created_at"]
    search_fields = ["code", "inviter__username", "invitee__username"]
    readonly_fields = ["code", "created_at"]

    @admin.display(description="邀请码")
    def code_short(self, obj):
        return obj.code[:16] + "..."


@admin.register(ViewLog)
class ViewLogAdmin(admin.ModelAdmin):
    list_display = ['post', 'fp_short', 'ip_short', 'created_at']
    list_filter = ['created_at']
    date_hierarchy = 'created_at'
    readonly_fields = ['post', 'fingerprint_hash', 'ip_hash', 'created_at']
    search_fields = ['post__title']

    @admin.display(description='指纹')
    def fp_short(self, obj):
        return obj.fingerprint_hash[:16] + '...'

    @admin.display(description='IP哈希')
    def ip_short(self, obj):
        return obj.ip_hash[:16] + '...'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Like)
class LikeAdmin(admin.ModelAdmin):
    list_display = ['user', 'content_type', 'object_id', 'created_time']
    list_filter = ['content_type', 'created_time']
    search_fields = ['user__username']
    date_hierarchy = 'created_time'
    readonly_fields = ['user', 'content_type', 'object_id', 'created_time']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['user', 'content_preview', 'content_type', 'object_id', 'created_time']
    list_filter = ['content_type', 'created_time']
    search_fields = ['user__username', 'content']
    date_hierarchy = 'created_time'
    readonly_fields = ['user', 'content_type', 'object_id', 'content', 'created_time', 'modified_time']

    @admin.display(description='评论内容')
    def content_preview(self, obj):
        return obj.content[:50]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BanAppeal)
class BanAppealAdmin(admin.ModelAdmin):
    list_display = ['user', 'content_preview', 'is_resolved', 'created_at', 'resolved_at']
    list_filter = ['is_resolved', 'created_at']
    search_fields = ['user__username', 'content']
    date_hierarchy = 'created_at'
    readonly_fields = ['user', 'content', 'created_at']

    @admin.display(description='申诉内容')
    def content_preview(self, obj):
        return obj.content[:80]

    def save_model(self, request, obj, form, change):
        if 'is_resolved' in form.changed_data and obj.is_resolved:
            obj.resolved_by = request.user
            obj.resolved_at = timezone.now()
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        return False
