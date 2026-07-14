"""
RSS Feed for latest published posts.
"""
from django.contrib.syndication.views import Feed
from django.urls import reverse

from .models import Post


class LatestPostsFeed(Feed):
    title = "Project MyCosmos"
    link = "/"
    description = "Project MyCosmos 最新文章，一个极简主义个人博客。"

    def items(self):
        return Post.objects.filter(status="published").order_by("-created_time")[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.excerpt

    def item_pubdate(self, item):
        return item.created_time

    def item_link(self, item):
        return reverse("post_detail", kwargs={"slug": item.slug})
