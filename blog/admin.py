"""
MeLi Cosmos v3.0 - Admin Configuration (multi-user).
"""
from django.contrib import admin

from .models import Category, Tag, Post, Memo, Series, UserProfile, InviteCode, ViewLog, Like, Comment


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
    list_display = ["user", "display_name", "title"]
    search_fields = ["user__username", "display_name"]


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
