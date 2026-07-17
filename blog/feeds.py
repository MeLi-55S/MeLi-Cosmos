"""
RSS Feeds for latest published posts (global + per-user).
"""
from django.contrib.syndication.views import Feed
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.contrib.auth.models import User

from .models import Post


class LatestPostsFeed(Feed):
    title = "MeLi Cosmos"
    link = "/"
    description = "MeLi Cosmos 最新文章，一个极简主义多用户博客平台。"

    def items(self):
        return Post.objects.filter(status="published").order_by("-created_time")[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.excerpt

    def item_pubdate(self, item):
        return item.created_time

    def item_link(self, item):
        return reverse("post_detail", kwargs={
            "username": item.author.username,
            "slug": item.slug,
        })


class UserPostsFeed(Feed):
    """Per-user RSS feed at /@<username>/feed/"""

    def get_object(self, request, username):
        return get_object_or_404(User, username=username)

    def title(self, obj):
        return f"{obj.username} - MeLi Cosmos"

    def link(self, obj):
        return reverse("user_space", kwargs={"username": obj.username})

    def description(self, obj):
        return f"{obj.username} 在 MeLi Cosmos 上的最新文章。"

    def items(self, obj):
        return Post.objects.filter(
            author=obj, status="published"
        ).order_by("-created_time")[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.excerpt

    def item_pubdate(self, item):
        return item.created_time

    def item_link(self, item):
        return reverse("post_detail", kwargs={
            "username": item.author.username,
            "slug": item.slug,
        })
