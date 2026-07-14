"""
Project MyCosmos v2.0 - Root URL Configuration.
"""
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include

from blog.sitemaps import PostSitemap, StaticViewSitemap

sitemaps = {
    "posts": PostSitemap,
    "static": StaticViewSitemap,
}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("", include("blog.urls")),
]
