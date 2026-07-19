"""
MeLi Cosmos v3.0 - Blog URL Routing (multi-user).
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
from .feeds import LatestPostsFeed, UserPostsFeed

urlpatterns = [
    # ── Global ──────────────────────────────────────────────────────
    path("", views.LandingView.as_view(), name="landing"),
    path("posts/", views.IndexView.as_view(), name="index"),
    path("feed/", LatestPostsFeed(), name="rss_feed"),
    path("search/", views.SearchView.as_view(), name="search"),
    path("about/", views.AboutView.as_view(), name="about"),
    path("rss/", views.RssGuideView.as_view(), name="rss_guide"),
    path("terms/", views.TermsView.as_view(), name="terms"),
    path("privacy/", views.PrivacyView.as_view(), name="privacy"),

    # ── Ban Appeal ─────────────────────────────────────────────────
    path("accounts/appeal/", views.BanAppealView.as_view(), name="ban_appeal"),

    # ── Auth ────────────────────────────────────────────────────────
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/invite/", views.InviteCodeGenerateView.as_view(), name="invite"),
    path("accounts/register/", views.InviteCodeEntryView.as_view(), name="register_entry"),
    path("accounts/register/<str:code>/", views.InviteRegisterView.as_view(), name="register"),
    path("accounts/profile/edit/", views.UserProfileUpdateView.as_view(), name="profile_edit"),
    path("accounts/profile/setup/", views.ProfileSetupView.as_view(), name="profile_setup"),
    path("accounts/avatar/", views.AvatarUpdateView.as_view(), name="avatar_edit"),

    # ── Post CRUD (no username prefix — author is the current user) ──
    path("post/create/", views.PostCreateView.as_view(), name="post_create"),
    path("post/<uuid:unique_id>/edit/", views.PostUpdateView.as_view(), name="post_edit"),
    path("post/<uuid:unique_id>/delete/", views.PostDeleteView.as_view(), name="post_delete"),
    path("post/<uuid:unique_id>/publish/", views.PostPublishView.as_view(), name="post_publish"),

    # ── User Space ──────────────────────────────────────────────────
    path("@<str:username>/", views.UserSpaceView.as_view(), name="user_space"),
    path("@<str:username>/post/<uslug:slug>/", views.PostDetailView.as_view(), name="post_detail"),
    path("@<str:username>/tag/<uslug:slug>/", views.PostByTagView.as_view(), name="user_post_by_tag"),
    path("@<str:username>/category/<uslug:slug>/", views.PostByCategoryView.as_view(), name="user_post_by_category"),
    path("@<str:username>/series/<uslug:slug>/", views.PostBySeriesView.as_view(), name="user_post_by_series"),
    path("@<str:username>/series/", views.SeriesListView.as_view(), name="user_series_list"),
    path("@<str:username>/archives/", views.ArchivesView.as_view(), name="user_archives"),
    path("@<str:username>/drafts/", views.DraftsView.as_view(), name="user_drafts"),
    path("@<str:username>/memos/", views.UserMemoListView.as_view(), name="user_memo_list"),
    path("@<str:username>/memo/<int:pk>/", views.MemoDetailView.as_view(), name="memo_detail"),
    path("@<str:username>/feed/", UserPostsFeed(), name="user_rss_feed"),

    # ── Global Memo ─────────────────────────────────────────────────
    path("memo/create/", views.MemoCreateView.as_view(), name="memo_create"),
    path("memos/", views.MemoListView.as_view(), name="memo_list"),

    # ── Series (create — author is current user) ────────────────────
    path("series/create/", views.SeriesCreateView.as_view(), name="series_create"),

    # ── Comments ────────────────────────────────────────────────────
    path("comment/create/", views.comment_create, name="comment_create"),

    # ── Safe Browsing ────────────────────────────────────────────────
    path("ajax/check-url/", views.check_url_ajax, name="check_url_ajax"),

    # ── AJAX helpers ─────────────────────────────────────────────────
    path("ajax/like/toggle/", views.like_toggle_ajax, name="like_toggle_ajax"),
    path("ajax/category/create/", views.category_create_ajax, name="category_create_ajax"),
    path("ajax/series/create/", views.series_create_ajax, name="series_create_ajax"),
    path("ajax/view/", views.view_count_ajax, name="view_count_ajax"),
    path("ajax/image/upload/", views.image_upload_ajax, name="image_upload_ajax"),
    path("ajax/avatar/upload/", views.avatar_upload_ajax, name="avatar_upload_ajax"),
    path("ajax/ping/", views.ping, name="ping"),

    # ── Legacy / Global (no username) ───────────────────────────────
    path("post/<uslug:slug>/", views.PostDetailView.as_view(), name="post_detail_legacy"),
    path("archives/", views.ArchivesView.as_view(), name="archives"),
    path("tag/<uslug:slug>/", views.PostByTagView.as_view(), name="post_by_tag"),
    path("category/<uslug:slug>/", views.PostByCategoryView.as_view(), name="post_by_category"),
    path("series/", views.SeriesListView.as_view(), name="series_list"),
]
