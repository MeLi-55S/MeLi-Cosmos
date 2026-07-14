"""
Project MyCosmos v2.0 - Blog URL Routing.
"""
from django.contrib.auth import views as auth_views
from django.urls import path, register_converter


class UnicodeSlugConverter:
    """Slug converter that supports Unicode characters (e.g. Chinese)."""
    regex = r'[-\w一-鿿]+'

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(UnicodeSlugConverter, "uslug")

from . import views
from .feeds import LatestPostsFeed

urlpatterns = [
    # Read
    path("", views.IndexView.as_view(), name="index"),

    # RSS Feed
    path("feed/", LatestPostsFeed(), name="rss_feed"),

    # Search
    path("search/", views.SearchView.as_view(), name="search"),

    # Post CRUD (must precede parameterized post/ patterns)
    path("post/create/", views.PostCreateView.as_view(), name="post_create"),
    path("post/<uuid:unique_id>/edit/", views.PostUpdateView.as_view(), name="post_edit"),
    path("post/<uuid:unique_id>/delete/", views.PostDeleteView.as_view(), name="post_delete"),
    path("post/<uuid:unique_id>/publish/", views.PostPublishView.as_view(), name="post_publish"),

    # Post detail by slug (numeric slugs are resolved in the view)
    path("post/<uslug:slug>/", views.PostDetailView.as_view(), name="post_detail"),

    # Authentication
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Memo
    path("memo/create/", views.MemoCreateView.as_view(), name="memo_create"),
    path("memos/", views.MemoListView.as_view(), name="memo_list"),

    # Navigation
    path("archives/", views.ArchivesView.as_view(), name="archives"),
    path("about/", views.AboutView.as_view(), name="about"),
    path("tag/<uslug:slug>/", views.PostByTagView.as_view(), name="post_by_tag"),
    path("category/<uslug:slug>/", views.PostByCategoryView.as_view(), name="post_by_category"),
    path("series/create/", views.SeriesCreateView.as_view(), name="series_create"),
    path("series/", views.SeriesListView.as_view(), name="series_list"),
    path("series/<uslug:slug>/", views.PostBySeriesView.as_view(), name="post_by_series"),
]
