"""
Sitemap configuration for search engine indexing.
"""
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Post, Category, Tag


class PostSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Post.objects.filter(status="published")

    def lastmod(self, obj):
        return obj.modified_time

    def location(self, obj):
        return reverse("post_detail", kwargs={"username": obj.author.username, "slug": obj.slug})


class StaticViewSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return ["index", "archives", "about", "memo_list"]

    def location(self, item):
        return reverse(item)
