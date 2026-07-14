"""
Project MyCosmos v2.0 - Admin Configuration.
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import Category, Tag, Post, Memo, Series


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


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
    list_filter = ["status", "category", "tags", "series", "created_time"]
    search_fields = ["title", "body", "unique_id"]
    prepopulated_fields = {"slug": ("title",)}
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
    list_display = ["name", "slug", "description"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Memo)
class MemoAdmin(admin.ModelAdmin):
    list_display = ["content_short", "is_public", "created_time"]
    list_filter = ["is_public", "created_time"]
    search_fields = ["content"]

    @admin.display(description="内容")
    def content_short(self, obj):
        return obj.content[:30]
