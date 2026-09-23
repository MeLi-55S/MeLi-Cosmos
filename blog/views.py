"""
MeLi Cosmos v2.0 → v3.0 - Blog Views.
Multi-user refactored Class-Based Views.
"""
from collections import OrderedDict
from datetime import date, timedelta

import hashlib
import nh3
import time
from io import BytesIO

import markdown as md_lib
from PIL import Image as PILImage

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.signing import Signer
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.utils import timezone
from django.views.generic import (
    ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView,
)

from .forms import PostForm, MemoForm, SeriesForm, UserProfileForm, CommentForm
from .mixins import AuthorRequiredMixin, AuthorOrPublishedMixin, UserSpaceMixin
from .models import Post, Memo, Category, Tag, Series, UserProfile, InviteCode, UploadedImage, ViewLog, Like, Comment, BanAppeal, Notification, get_or_create_guest_avatar


# ═══════════════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════════════

def _get_related_posts(post, max_results=3):
    """Score related posts by category match (+3) and tag overlap (+2 each)."""
    candidates = Post.objects.filter(
        status="published"
    ).exclude(pk=post.pk).select_related(
        "category", "author", "author__profile"
    ).prefetch_related("tags").order_by("-modified_time")[:50]

    current_category = post.category
    current_tag_ids = {t.id for t in post.tags.all()}

    if not current_category and not current_tag_ids:
        return []

    scored = []
    for candidate in candidates:
        score = 0
        if current_category and candidate.category_id == current_category.pk:
            score += 3
        candidate_tag_ids = {t.id for t in candidate.tags.all()}
        overlap = len(current_tag_ids & candidate_tag_ids)
        score += overlap * 2
        if score > 0:
            scored.append((score, candidate))

    scored.sort(key=lambda x: (-x[0], -x[1].created_time.timestamp()))
    return [c for _, c in scored[:max_results]]


def _safe_redirect(url, fallback='/', fragment=''):
    """Validate redirect URL against allowed hosts to prevent open redirect.
    Optional fragment is appended after host validation (e.g. '#comments')."""
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts=None):
        target = url
    else:
        target = fallback
    if fragment:
        # Avoid double fragments
        if '#' not in target:
            target += fragment
    return redirect(target)


# ── Signed cookie helpers for pending (in-moderation) comments ──

_PENDING_COOKIE_NAME = 'pcids'         # short name keeps cookie small
_PENDING_COOKIE_MAX_AGE = 60 * 60 * 24 * 30   # 30 days
_pending_signer = Signer(salt='pending_comments')

def _get_pending_ids_from_cookie(request):
    """Return a set of pending comment IDs from the signed cookie.
    Auto-cleans IDs whose comments are now visible (already approved)."""
    raw = request.COOKIES.get(_PENDING_COOKIE_NAME)
    if not raw:
        return set()
    try:
        ids_str = _pending_signer.unsign(raw)
    except Exception:
        return set()
    stale = set()
    valid = set()
    for s in ids_str.split(','):
        try:
            pk = int(s)
        except (ValueError, TypeError):
            continue
        valid.add(pk)
    if not valid:
        return valid
    # Remove IDs that are already approved
    already_visible = set(
        Comment.objects.filter(pk__in=valid, is_visible=True)
        .values_list('pk', flat=True)
    )
    valid -= already_visible
    # Remove IDs that no longer exist (deleted)
    existing = set(
        Comment.objects.filter(pk__in=valid)
        .values_list('pk', flat=True)
    )
    valid &= existing
    return valid

def _set_pending_ids_cookie(response, ids):
    """Sign the given set of IDs and set the cookie on the response."""
    if ids:
        value = _pending_signer.sign(','.join(str(i) for i in sorted(ids, key=int)))
    else:
        value = ''
    response.set_cookie(
        _PENDING_COOKIE_NAME,
        value,
        max_age=_PENDING_COOKIE_MAX_AGE if ids else 0,  # delete cookie if empty
        httponly=True,
        samesite='Lax',
    )

def _add_pending_id_cookie(request, response, comment_id):
    """Append a comment ID to the pending cookie and set it."""
    ids = _get_pending_ids_from_cookie(request)
    ids.add(int(comment_id))
    _set_pending_ids_cookie(response, ids)


def _format_like_display(names, count):
    if count == 0:
        return ""
    if count <= 2:
        return "、".join(names) + " 赞了"
    return f"{names[0]}、{names[1]}等{count}人赞了"


def get_like_state(content_type, object_id, user=None):
    """某个对象上的点赞汇总：数量、前两名点赞者、展示文案、当前用户是否已赞。

    Web 文章页/碎碎念页、``/ajax/like/toggle/`` 与 ``/api/v1`` 共用这一份实现，
    避免文案口径出现第二套。
    """
    likes = Like.objects.filter(
        content_type=content_type, object_id=object_id
    ).select_related("user__profile")
    count = likes.count()
    names = [
        like.user.profile.display_name or like.user.username
        for like in likes.order_by("-created_time")[:2]
    ]
    liked = bool(
        user is not None
        and user.is_authenticated
        and likes.filter(user=user).exists()
    )
    return {
        "count": count,
        "names": names,
        "display_text": _format_like_display(names, count),
        "user_liked": liked,
    }


# Markdown rendering
_ALLOWED_MD_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "a", "img", "em", "strong", "code", "pre",
    "div", "span", "ul", "ol", "li", "blockquote",
    "table", "thead", "tbody", "tr", "th", "td",
    "del", "sup", "sub", "dl", "dt", "dd",
})

_ALLOWED_MD_ATTRS = {tag: {"class", "id"} for tag in _ALLOWED_MD_TAGS}
_ALLOWED_MD_ATTRS["a"].update({"href", "title"})
_ALLOWED_MD_ATTRS["img"].update({"src", "alt", "title"})
# tags which don't have useful id/class attributes
for _tag in ("br", "hr", "em", "strong", "del", "sup", "sub", "li", "ul", "ol", "blockquote"):
    _ALLOWED_MD_ATTRS[_tag] = set()


def _sanitize_html(html):
    """Sanitize rendered Markdown HTML to prevent XSS."""
    return nh3.clean(
        html,
        tags=_ALLOWED_MD_TAGS,
        attributes=_ALLOWED_MD_ATTRS,
        url_schemes={"http", "https", "mailto"},
    )


def _render_markdown(text):
    """Convert Markdown text to sanitized HTML and TOC."""
    md_extensions = getattr(
        settings, "MARKDOWN_EXTENSIONS", ["extra", "codehilite", "fenced_code"]
    )
    md = md_lib.Markdown(extensions=md_extensions)
    raw_html = md.convert(text)
    return _sanitize_html(raw_html), _sanitize_html(md.toc)


# ═══════════════════════════════════════════════════════════════════════════
# Landing & Index
# ═══════════════════════════════════════════════════════════════════════════

class LandingView(TemplateView):
    template_name = "blog/landing.html"


class IndexView(ListView):
    model = Post
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 10

    def get_queryset(self):
        qs = Post.objects.select_related("category", "author", "author__profile").prefetch_related("tags")
        if self.request.user.is_authenticated:
            return qs.filter(
                Q(status="published") | Q(author=self.request.user)
            ).order_by("-modified_time")
        return qs.filter(status="published")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context["dashboard"] = {
                "total_posts": Post.objects.filter(author=self.request.user).count(),
                "total_views": Post.objects.filter(
                    author=self.request.user, status="published"
                ).aggregate(total=Sum("views"))["total"] or 0,
                "published_count": Post.objects.filter(
                    author=self.request.user, status="published"
                ).count(),
                "draft_count": Post.objects.filter(
                    author=self.request.user, status="draft"
                ).count(),
            }
        return context


class SearchView(ListView):
    model = Post
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 10

    def get_queryset(self):
        q = self.request.GET.get("q", "").strip()
        if q:
            return Post.objects.filter(
                status="published",
            ).filter(
                Q(title__icontains=q) | Q(body__icontains=q)
            ).select_related("category", "author", "author__profile").prefetch_related("tags")
        return Post.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_filter"] = f'搜索："{self.request.GET.get("q", "")}"'
        return context


# ═══════════════════════════════════════════════════════════════════════════
# Post Detail
# ═══════════════════════════════════════════════════════════════════════════

def _get_series_nav(post):
    """返回文章在其所属系列中的前后篇信息，无系列则为 None。

    被 Web 模板（``post_detail.html``）与 API（``blog/api/serializers.py``）共用，
    避免两处各写一份排序逻辑。返回的 prev/next 是 ``only()`` 的轻量 Post。
    """
    if not post.series:
        return None
    series_posts = list(Post.objects.filter(
        series=post.series, status="published"
    ).select_related("author").order_by("series_order", "created_time").only(
        "id", "slug", "title", "series_order", "author__username"))
    for i, sp in enumerate(series_posts):
        if sp.pk == post.pk:
            return {
                "series": post.series,
                "index": i + 1,
                "total": len(series_posts),
                "prev": series_posts[i - 1] if i > 0 else None,
                "next": series_posts[i + 1] if i < len(series_posts) - 1 else None,
            }
    return None


class PostDetailView(DetailView):
    model = Post
    template_name = "blog/post_detail.html"
    context_object_name = "post"

    def get_object(self, queryset=None):
        slug_or_pk = self.kwargs.get("slug")
        username = self.kwargs.get("username")

        qs = Post.objects.select_related("category", "author", "author__profile").prefetch_related("tags")

        if username:
            author = get_object_or_404(User, username=username)
            qs = qs.filter(author=author)

        try:
            pk = int(slug_or_pk)
            post = qs.filter(pk=pk).first()
            if post is not None:
                return post
        except (ValueError, TypeError):
            pass

        post = get_object_or_404(qs, slug=slug_or_pk)

        if post.status != "published" and post.author != self.request.user:
            raise Http404("Post not found")
        return post

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        post = self.object

        post.body_html, toc = _render_markdown(post.body)
        post.toc = toc if '<li>' in toc else ''

        char_count = len(post.body)
        read_time = max(1, round(char_count / 1000))
        context["read_time"] = read_time

        # Series navigation
        nav = _get_series_nav(post)
        if nav:
            context["series_prev"] = nav["prev"]
            context["series_next"] = nav["next"]
            context["series_name"] = nav["series"].name
            context["series_slug"] = nav["series"].slug
            context["series_index"] = nav["index"]
            context["series_total"] = nav["total"]

        # Related posts (scored)
        context["related_posts"] = _get_related_posts(post)

        # Likes
        ct = ContentType.objects.get_for_model(Post)
        state = get_like_state(ct, post.pk, self.request.user)
        context["likes_count"] = state["count"]
        context["likes_users"] = state["names"]
        context["likes_display_text"] = state["display_text"]
        context["user_liked"] = state["user_liked"]

        # Comments (visible + user's own pending comment)
        comments = Comment.objects.filter(
            content_type=ct, object_id=post.pk
        ).select_related("user__profile")

        # Show user's own pending comment (if any) with "审核中" badge
        pending_ids = _get_pending_ids_from_cookie(self.request)
        if pending_ids:
            pending = Comment.objects.filter(
                pk__in=pending_ids, content_type=ct, object_id=post.pk, is_visible=False
            ).select_related("user__profile").first()
            if pending:
                context["pending_comment"] = pending

        context["comments"] = comments.filter(is_visible=True)

        # Recover form data from session (after validation error redirect)
        form_data = self.request.session.pop('comment_form_data', None)
        form_errors_session = self.request.session.pop('comment_form_errors', None)
        if form_data:
            context["comment_form"] = CommentForm(initial=form_data,
                                                  is_guest=not self.request.user.is_authenticated)
            if form_errors_session:
                context["form_errors_from_session"] = form_errors_session
        else:
            context["comment_form"] = CommentForm(is_guest=not self.request.user.is_authenticated)

        context["content_type_key"] = "blog.post"
        context["object_id"] = post.pk

        return context


# ═══════════════════════════════════════════════════════════════════════════
# Post CRUD
# ═══════════════════════════════════════════════════════════════════════════

class PostCreateView(LoginRequiredMixin, CreateView):
    model = Post
    form_class = PostForm
    template_name = "blog/post_form.html"

    def get_initial(self):
        initial = super().get_initial()
        series_id = self.request.GET.get("series")
        if series_id:
            try:
                initial["series"] = Series.objects.get(id=series_id, author=self.request.user)
            except (Series.DoesNotExist, ValueError):
                pass
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["existing_tag_names"] = list(
            Tag.objects.filter(author=self.request.user).values_list("name", flat=True).order_by("name")
        )
        return context

    def form_valid(self, form):
        form.instance.author = self.request.user
        try:
            return super().form_valid(form)
        except IntegrityError:
            form.add_error(None, "你已有一篇相同标题（或相同 URL 别名）的文章，请修改标题。")
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse("post_detail", kwargs={
            "username": self.object.author.username,
            "slug": self.object.slug,
        })


class PostUpdateView(LoginRequiredMixin, AuthorRequiredMixin, UpdateView):
    model = Post
    form_class = PostForm
    template_name = "blog/post_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["existing_tag_names"] = list(
            Tag.objects.filter(author=self.request.user).values_list("name", flat=True).order_by("name")
        )
        return context

    def get_object(self, queryset=None):
        return get_object_or_404(Post, unique_id=self.kwargs["unique_id"])

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except IntegrityError:
            form.add_error(None, "你已有一篇相同标题（或相同 URL 别名）的文章，请修改标题。")
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse("post_detail", kwargs={
            "username": self.object.author.username,
            "slug": self.object.slug,
        })


class PostDeleteView(LoginRequiredMixin, AuthorRequiredMixin, DeleteView):
    model = Post
    template_name = "blog/post_confirm_delete.html"
    context_object_name = "post"

    def get_object(self, queryset=None):
        return get_object_or_404(Post, unique_id=self.kwargs["unique_id"])

    def get_success_url(self):
        return reverse("index")


class PostPublishView(LoginRequiredMixin, AuthorRequiredMixin, DetailView):
    model = Post
    http_method_names = ["post"]

    def get_object(self, queryset=None):
        return get_object_or_404(Post, unique_id=self.kwargs["unique_id"])

    def post(self, request, *args, **kwargs):
        post = self.get_object()
        post.status = "published"
        post.created_time = timezone.now()
        post.save(update_fields=["status", "created_time"])
        return redirect(reverse("post_detail", kwargs={
            "username": post.author.username,
            "slug": post.slug,
        }))


# ═══════════════════════════════════════════════════════════════════════════
# Memo
# ═══════════════════════════════════════════════════════════════════════════

class MemoListView(ListView):
    model = Memo
    template_name = "blog/memo_list.html"
    context_object_name = "memos"
    paginate_by = 20

    def get_queryset(self):
        qs = Memo.objects.select_related("author", "author__profile").all()
        if not self.request.user.is_authenticated:
            qs = qs.filter(is_public=True)
        return qs


class UserMemoListView(UserSpaceMixin, ListView):
    """Per-user memo list."""
    model = Memo
    template_name = "blog/includes/user_layout.html"
    context_object_name = "memos"
    paginate_by = 20
    active_tab = "memos"
    content_template = "blog/includes/memo_list_content.html"

    def get_queryset(self):
        qs = Memo.objects.filter(author=self.space_owner)
        if self.space_owner != self.request.user:
            qs = qs.filter(is_public=True)
        return qs.select_related("author", "author__profile")


class MemoCreateView(LoginRequiredMixin, CreateView):
    model = Memo
    form_class = MemoForm
    template_name = "blog/memo_form.html"

    def form_valid(self, form):
        form.instance.author = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
            return next_url
        return reverse("memo_list")


# ═══════════════════════════════════════════════════════════════════════════
# Navigation Pages
# ═══════════════════════════════════════════════════════════════════════════

class ArchivesView(UserSpaceMixin, ListView):
    model = Post
    template_name = "blog/archives.html"
    context_object_name = "posts"
    active_tab = "archives"
    content_template = "blog/includes/archives_list.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        if hasattr(self, "space_owner"):
            self.template_name = "blog/includes/user_layout.html"

    def get_queryset(self):
        username = self.kwargs.get("username")
        qs = Post.objects.filter(status="published").select_related(
            "category", "author", "author__profile"
        ).only("title", "slug", "created_time", "category__name",
               "author__username", "author__profile__avatar")
        if username:
            qs = qs.filter(author__username=username)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        archives = OrderedDict()
        for post in context["posts"]:
            key = (post.created_time.year, post.created_time.month)
            archives.setdefault(key, []).append(post)
        context["archives"] = archives
        context["archive_owner"] = self.kwargs.get("username")
        return context


class AboutView(TemplateView):
    template_name = "blog/about.html"

    def get_context_data(self, **kwargs):
        import platform
        import time
        from django.db import connection

        # DB latency
        start = time.monotonic()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_latency_ms = round((time.monotonic() - start) * 1000, 2)

        context = super().get_context_data(**kwargs)
        context["status"] = {
            "hostname": getattr(settings, "SERVER_DISPLAY_NAME", "") or platform.node(),
            "python_version": platform.python_version(),
            "db_latency_ms": db_latency_ms,
            "db_engine": connection.settings_dict["ENGINE"].split(".")[-1],
        }
        return context


class RssGuideView(TemplateView):
    template_name = "blog/rss_guide.html"


class TermsView(TemplateView):
    template_name = "blog/terms.html"


class PrivacyView(TemplateView):
    template_name = "blog/privacy.html"


class SeriesCreateView(LoginRequiredMixin, CreateView):
    model = Series
    form_class = SeriesForm
    template_name = "blog/series_form.html"

    def form_valid(self, form):
        form.instance.author = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("user_series_list", kwargs={"username": self.request.user.username})


class SeriesListView(UserSpaceMixin, ListView):
    model = Series
    template_name = "blog/series_list.html"
    context_object_name = "series_list"
    active_tab = "series"
    content_template = "blog/includes/series_list_content.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        if hasattr(self, "space_owner"):
            self.template_name = "blog/includes/user_layout.html"

    def get_queryset(self):
        if hasattr(self, "space_owner"):
            return Series.objects.filter(
                author=self.space_owner
            ).prefetch_related("post_set").select_related("author")
        return Series.objects.prefetch_related("post_set").select_related("author").all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["series_owner"] = self.kwargs.get("username")
        return context


class PostByTagView(ListView):
    model = Post
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 10

    def get_queryset(self):
        username = self.kwargs.get("username")
        slug = self.kwargs["slug"]

        if username:
            user = get_object_or_404(User, username=username)
            self.tag = get_object_or_404(Tag, slug=slug, author=user)
            self.tag_name = self.tag.name
        else:
            tags = Tag.objects.filter(slug=slug)
            if not tags.exists():
                raise Http404("No Tag matches the given query.")
            self.tag_name = tags.first().name
            return Post.objects.filter(
                status="published", tags__in=tags
            ).select_related("category", "author", "author__profile").prefetch_related("tags").distinct()

        return Post.objects.filter(
            status="published", tags=self.tag
        ).select_related("category", "author", "author__profile").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_filter"] = f"标签：{self.tag_name}"
        return context


class PostBySeriesView(UserSpaceMixin, ListView):
    model = Post
    template_name = "blog/series_detail.html"
    context_object_name = "posts"
    paginate_by = None
    active_tab = "series"
    content_template = "blog/includes/series_reader_content.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        slug = self.kwargs["slug"]
        if hasattr(self, "space_owner"):
            self.series = get_object_or_404(Series, slug=slug, author=self.space_owner)
            self.series_owner = self.space_owner
        else:
            self.series = Series.objects.filter(slug=slug).order_by("pk").first()
            if self.series is None:
                raise Http404(f"No series found with slug '{slug}'")
            self.series_owner = self.series.author

        self.sort_order = self.request.GET.get("sort", "asc")

    def get_queryset(self):
        ordering = "created_time" if self.sort_order == "asc" else "-created_time"
        return Post.objects.filter(
            status="published", series=self.series
        ).select_related("category", "author", "author__profile", "series").prefetch_related("tags").order_by(ordering)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        posts = list(context["posts"])
        context["series"] = self.series
        context["series_owner"] = self.series_owner
        context["series_posts"] = posts
        context["sort_order"] = self.sort_order

        # Determine which post to display
        current_slug = self.request.GET.get("post")
        current_post = None
        if current_slug:
            current_post = next((p for p in posts if p.slug == current_slug), None)
        if current_post is None and posts:
            current_post = posts[0]

        context["current_post"] = current_post
        if current_post:
            context["current_post_body_html"] = _render_markdown(current_post.body)[0]
            # Find index and prev/next
            try:
                idx = posts.index(current_post)
            except ValueError:
                idx = 0
            context["current_index"] = idx + 1
            context["prev_post"] = posts[idx - 1] if idx > 0 else None
            context["next_post"] = posts[idx + 1] if idx < len(posts) - 1 else None

        if self.request.user == self.series_owner:
            context["author_other_posts"] = Post.objects.filter(
                author=self.request.user, status="published"
            ).exclude(series=self.series).only("id", "title").order_by("-created_time")[:50]

        return context


class PostByCategoryView(ListView):
    model = Post
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 10

    def get_queryset(self):
        username = self.kwargs.get("username")
        slug = self.kwargs["slug"]

        if username:
            user = get_object_or_404(User, username=username)
            self.category = get_object_or_404(Category, slug=slug, author=user)
        else:
            self.category = get_object_or_404(Category, slug=slug)

        return Post.objects.filter(
            status="published", category=self.category
        ).select_related("category", "author", "author__profile").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_filter"] = f"分类：{self.category.name}"
        return context


# ═══════════════════════════════════════════════════════════════════════════
# User Space
# ═══════════════════════════════════════════════════════════════════════════

class UserSpaceView(UserSpaceMixin, ListView):
    """User profile / personal blog space at /@<username>/"""
    model = Post
    template_name = "blog/includes/user_layout.html"
    context_object_name = "posts"
    paginate_by = 10
    active_tab = "posts"
    content_template = "blog/includes/user_posts.html"

    def get_queryset(self):
        qs = Post.objects.filter(author=self.space_owner).select_related(
            "category", "author"
        ).prefetch_related("tags")
        if self.space_owner != self.request.user:
            qs = qs.filter(status="published")
        return qs


class DraftsView(LoginRequiredMixin, UserSpaceMixin, ListView):
    """Show draft posts for the space owner (owner only)."""
    model = Post
    template_name = "blog/includes/user_layout.html"
    context_object_name = "posts"
    paginate_by = 10
    active_tab = "drafts"
    content_template = "blog/includes/user_posts.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user != self.space_owner:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Post.objects.filter(
            author=self.space_owner, status="draft"
        ).select_related("category", "author").prefetch_related("tags")


# ═══════════════════════════════════════════════════════════════════════════
# Auth: Invite & Registration
# ═══════════════════════════════════════════════════════════════════════════

def invite_daily_limit_reached(user):
    """非 staff 用户每天只能生成 INVITE_DAILY_LIMIT 个未使用的邀请码。"""
    if user.is_staff:
        return False
    today_unused = InviteCode.objects.filter(
        inviter=user,
        created_at__date=date.today(),
        is_used=False,
    ).count()
    return today_unused >= getattr(settings, "INVITE_DAILY_LIMIT", 1)


def issue_invite_code(user):
    """生成邀请码的唯一实现：频次校验 → 建码。超限时抛 ContentActionError。"""
    if invite_daily_limit_reached(user):
        raise ContentActionError(
            "今日邀请码生成已达上限（1个/天），请明天再试。",
            status=429, code="invite_daily_limit",
        )
    expire_hours = getattr(settings, "INVITE_CODE_EXPIRE_HOURS", 24)
    return InviteCode.objects.create(
        code=InviteCode.generate_code(),
        inviter=user,
        expires_at=timezone.now() + timedelta(hours=expire_hours),
    )


def get_usable_invite(code):
    """按码字符串取出可用邀请码（存在、未用、未过期），否则抛 ContentActionError。"""
    try:
        invite = InviteCode.objects.select_related("inviter").get(code=code)
    except InviteCode.DoesNotExist:
        raise ContentActionError("无效的邀请码，请检查后重试。",
                                 status=404, code="invite_not_found")
    if invite.is_used:
        raise ContentActionError("此邀请码已被使用。", status=409, code="invite_used")
    if invite.is_expired:
        raise ContentActionError("此邀请码已过期。", status=410, code="invite_expired")
    return invite


def consume_invite(invite, user):
    """原子地把邀请码标记为已被该用户使用；并发下已被占用则返回 False。"""
    updated = InviteCode.objects.filter(
        pk=invite.pk, is_used=False
    ).update(is_used=True, invitee=user, used_at=timezone.now())
    if updated:
        invite.refresh_from_db()
    return bool(updated)


class InviteCodeGenerateView(LoginRequiredMixin, ListView):
    """Invite code management: list + generate."""
    template_name = "blog/invite.html"
    context_object_name = "invite_codes"
    paginate_by = 20

    def get_queryset(self):
        return InviteCode.objects.filter(
            inviter=self.request.user
        ).select_related("invitee").order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invite_expire_hours"] = getattr(settings, "INVITE_CODE_EXPIRE_HOURS", 24)

        # Frequency check: non-staff limited to 1 unused code per day
        if not self.request.user.is_staff:
            today_unused = InviteCode.objects.filter(
                inviter=self.request.user,
                created_at__date=date.today(),
                is_used=False,
            ).count()
            context["can_generate"] = today_unused < getattr(settings, "INVITE_DAILY_LIMIT", 1)
        else:
            context["can_generate"] = True

        return context

    def post(self, request, *args, **kwargs):
        try:
            code = issue_invite_code(request.user)
        except ContentActionError as exc:
            messages.error(request, exc.message)
            return redirect("invite")
        messages.success(request, f"邀请码已生成：{code.code}")
        return redirect("invite")


class InviteRegisterView(CreateView):
    """Registration with invite code validation."""
    model = User
    form_class = UserCreationForm
    template_name = "blog/register.html"

    def dispatch(self, request, *args, **kwargs):
        self.invite_code = get_object_or_404(
            InviteCode.objects.select_related("inviter"),
            code=self.kwargs["code"],
        )
        if self.invite_code.is_used:
            messages.error(request, "此邀请码已被使用。")
            return redirect("login")
        if self.invite_code.is_expired:
            messages.error(request, "此邀请码已过期。")
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invite_code"] = self.invite_code
        context["inviter"] = self.invite_code.inviter
        return context

    def form_valid(self, form):
        user = form.save()
        # Atomically mark invite code as used (prevents double-use race)
        if not consume_invite(self.invite_code, user):
            user.delete()
            messages.error(self.request, "邀请码已被使用。")
            return redirect("login")
        # Auto-login
        login(self.request, user)
        messages.success(self.request, f"欢迎加入 MeLi Cosmos，{user.username}！")
        # Respect next parameter, fallback to profile setup. Skip landing page.
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
            return redirect(next_url)
        return redirect("profile_setup")


class UserProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = UserProfile
    form_class = UserProfileForm
    template_name = "blog/profile_edit.html"

    def get_object(self, queryset=None):
        return self.request.user.profile

    def get_success_url(self):
        return reverse("user_space", kwargs={"username": self.request.user.username})


class ProfileSetupView(LoginRequiredMixin, UpdateView):
    """First-time profile setup after registration — includes skip button."""
    model = UserProfile
    form_class = UserProfileForm
    template_name = "blog/profile_setup.html"

    def get_object(self, queryset=None):
        return self.request.user.profile

    def get_success_url(self):
        return reverse("user_space", kwargs={"username": self.request.user.username})


class AvatarUpdateView(LoginRequiredMixin, TemplateView):
    """Avatar upload page with crop/preview."""
    template_name = "blog/avatar_edit.html"


def store_avatar(user, uploaded):
    """头像上传的唯一实现：大小校验 → 处理成 WebP → 覆盖旧文件 → 落库。

    成功返回 {'url', 'width', 'height'}；任何一步不合法都抛 ContentActionError，
    由调用方决定是回 JSON 还是 messages。
    """
    if uploaded is None:
        raise ContentActionError("未选择文件", status=400, code="no_file")
    if uploaded.size > MAX_UPLOAD_SIZE:
        raise ContentActionError("文件超过10MB限制", status=400, code="too_large")

    raw = uploaded.read()
    try:
        webp_data, w, h = _process_image(raw)
    except Exception:
        raise ContentActionError("无法识别的图片文件", status=400, code="bad_image")

    profile = user.profile
    if profile.avatar:
        profile.avatar.delete(save=False)
    profile.avatar.save(f"{user.username}.webp", ContentFile(webp_data), save=True)
    return {"url": profile.avatar.url, "width": w, "height": h}


@require_POST
def avatar_upload_ajax(request):
    """Receive cropped avatar canvas data, process, save to profile."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)
    try:
        payload = store_avatar(request.user, request.FILES.get("image"))
    except ContentActionError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)
    return JsonResponse(payload)


class InviteCodeEntryView(TemplateView):
    """Landing page for entering an invite code manually."""
    template_name = "blog/register_entry.html"

    def post(self, request, *args, **kwargs):
        code = request.POST.get("code", "").strip()
        if not code:
            messages.error(request, "请输入邀请码。")
            return self.render_to_response(self.get_context_data())
        try:
            get_usable_invite(code)
        except ContentActionError as exc:
            messages.error(request, exc.message)
            return self.render_to_response(self.get_context_data())
        next_url = request.POST.get("next", "")
        url = reverse("register", kwargs={"code": code})
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
            url += "?next=" + next_url
        return redirect(url)


# ── View Counting ─────────────────────────────────────────────────────

def _view_identity_hashes(request, fingerprint):
    """把访客的 IP / 浏览器指纹哈希成 ViewLog 用的两列（带 SECRET_KEY 盐，不可逆）。"""
    ip = _get_client_ip(request)
    salt = settings.SECRET_KEY
    ip_hash = hashlib.sha256((salt + ip).encode()).hexdigest()
    fp_hash = hashlib.sha256((salt + (fingerprint or "")).encode()).hexdigest()[:64]
    return ip_hash, fp_hash


def record_post_view(request, post, fingerprint, *, use_session=True):
    """浏览计数的唯一实现：会话冷却 → 指纹/IP 冷却 → ``views+1`` + 落 ViewLog。

    返回是否计数。Web 端带 session（同一浏览器会话内一篇只算一次）；
    API 端没有会话概念，用 ``use_session=False``，冷却完全靠指纹/IP。
    """
    from datetime import timedelta

    session_key = f"post_viewed_{post.pk}"
    if use_session and request.session.get(session_key):
        return False

    ip_hash, fp_hash = _view_identity_hashes(request, fingerprint)

    # Primary: fingerprint OR IP cooldown check (either match → block)
    cooldown_hours = getattr(settings, "VIEW_LOG_COOLDOWN_HOURS", 1)
    cutoff = timezone.now() - timedelta(hours=cooldown_hours)
    if ViewLog.objects.filter(post_id=post.pk, created_at__gte=cutoff).filter(
        Q(fingerprint_hash=fp_hash) | Q(ip_hash=ip_hash)
    ).exists():
        return False

    with transaction.atomic():
        Post.objects.filter(pk=post.pk).update(views=F("views") + 1)
        ViewLog.objects.create(
            post_id=post.pk,
            fingerprint_hash=fp_hash,
            ip_hash=ip_hash,
        )
    if use_session:
        request.session[session_key] = True
    return True


@require_POST
@csrf_exempt
def view_count_ajax(request):
    """Record a post view based on browser fingerprint + IP hash + session."""
    import json

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"counted": False}, status=400)

    post_id = data.get("post_id")
    fingerprint = data.get("fingerprint", "")

    if not post_id or not fingerprint:
        return JsonResponse({"counted": False}, status=400)

    post = Post.objects.filter(pk=post_id).first()
    if post is None:
        return JsonResponse({"counted": False}, status=400)

    return JsonResponse({"counted": record_post_view(request, post, fingerprint)})


# ── AJAX helpers ──────────────────────────────────────────────────────

@require_POST
def category_create_ajax(request):
    """Create a new Category for the current user via AJAX."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "分类名不能为空"}, status=400)
    cat, created = Category.get_or_create_for_author(name, request.user)
    return JsonResponse({"id": cat.pk, "name": cat.name, "slug": cat.slug, "created": created})


@require_POST
def series_create_ajax(request):
    """Create a new Series for the current user via AJAX."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "系列名不能为空"}, status=400)
    ser, created = Series.get_or_create_for_author(name, request.user)
    return JsonResponse({"id": ser.pk, "name": ser.name, "slug": ser.slug, "created": created})


def manage_series_posts(user, series_id, action, post_id):
    """把文章挂进/移出系列，刻意不碰 ``modified_time``（auto_now）。

    Web 的 AJAX 与 API 共用这一份实现；返回受影响的文章。
    失败抛 ``ContentActionError``，由调用方决定回 JSON 还是 messages。
    """
    if action not in ("add", "remove") or not post_id:
        raise ContentActionError("参数不完整", 400, "missing_argument")
    try:
        series_id = int(series_id)
        post_id = int(post_id)
    except (TypeError, ValueError):
        raise ContentActionError("参数不完整", 400, "missing_argument")
    series = Series.objects.filter(id=series_id, author=user).first()
    if series is None:
        raise ContentActionError("系列不存在", 404, "not_found")
    post = Post.objects.filter(id=post_id, author=user).first()
    if post is None:
        raise ContentActionError("文章不存在", 404, "not_found")
    if action == "add":
        Post.objects.filter(pk=post.pk).update(series=series)
    else:
        Post.objects.filter(pk=post.pk).update(series=None, series_order=1)
    return post


@require_POST
def series_manage_posts_ajax(request, series_id):
    """Add or remove a post from a series without touching modified_time."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)
    try:
        post = manage_series_posts(request.user, series_id,
                                   request.POST.get("action"), request.POST.get("post_id"))
    except ContentActionError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)
    return JsonResponse({"ok": True, "action": request.POST.get("action"),
                         "post_title": post.title})


# ── Image Upload helpers ──────────────────────────────────────────────

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_IMAGE_DIMENSION = 2000
WEBP_QUALITY = 80


def _compute_hashes(data):
    return hashlib.md5(data).hexdigest(), hashlib.sha1(data).hexdigest()


def _process_image(data):
    """Validate, strip metadata, resize, and convert to WebP.
    Returns (webp_bytes, width, height).
    """
    img = PILImage.open(BytesIO(data))
    # Validate it's actually an image
    img.verify()
    # Re-open after verify()
    img = PILImage.open(BytesIO(data))

    # Convert RGBA to RGB for WebP
    if img.mode in ("RGBA", "P"):
        rgb = PILImage.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        rgb.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = rgb

    # Strip EXIF by creating a clean copy
    clean = PILImage.new(img.mode, img.size)
    clean.putdata(list(img.getdata()))

    # Resize if too large (maintain aspect ratio)
    w, h = clean.size
    if max(w, h) > MAX_IMAGE_DIMENSION:
        ratio = MAX_IMAGE_DIMENSION / max(w, h)
        clean = clean.resize((int(w * ratio), int(h * ratio)), PILImage.LANCZOS)
        w, h = clean.size

    buf = BytesIO()
    clean.save(buf, format="WEBP", quality=WEBP_QUALITY)
    return buf.getvalue(), w, h


@require_POST
def post_autosave_ajax(request):
    """AJAX auto-save: create a new draft or update an existing post."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=401)

    unique_id = request.POST.get("unique_id", "").strip()
    title = request.POST.get("title", "").strip()
    body = request.POST.get("body", "")
    cover = request.POST.get("cover", "").strip()
    excerpt = request.POST.get("excerpt", "").strip()
    category_id = request.POST.get("category", "").strip()
    series_id = request.POST.get("series", "").strip()
    license_val = request.POST.get("license", "CC BY-NC 4.0").strip()
    tag_names_raw = request.POST.get("tag_names", "").strip()

    if unique_id:
        try:
            post = Post.objects.get(unique_id=unique_id, author=request.user)
        except Post.DoesNotExist:
            return JsonResponse({"error": "文章不存在"}, status=404)
        post.title = title or post.title
        post.body = body
        post.cover = cover
        post.excerpt = excerpt
        if category_id:
            try:
                post.category = Category.objects.get(id=int(category_id), author=request.user)
            except (Category.DoesNotExist, ValueError):
                pass
        else:
            post.category = None
        if series_id:
            try:
                post.series = Series.objects.get(id=int(series_id), author=request.user)
            except (Series.DoesNotExist, ValueError):
                pass
        else:
            post.series = None
        post.license = license_val
        post.save()
    else:
        if not title:
            title = "未命名草稿"
        post = Post(
            author=request.user,
            title=title,
            body=body,
            cover=cover,
            excerpt=excerpt,
            license=license_val,
            status="draft",
        )
        if category_id:
            try:
                post.category = Category.objects.get(id=int(category_id), author=request.user)
            except (Category.DoesNotExist, ValueError):
                pass
        if series_id:
            try:
                post.series = Series.objects.get(id=int(series_id), author=request.user)
            except (Series.DoesNotExist, ValueError):
                pass
        post.save()

    # Sync tags
    if tag_names_raw:
        tags = [
            Tag.get_or_create_for_author(name, request.user)[0]
            for name in parse_tag_names(tag_names_raw)
        ]
        post.tags.set(tags)

    return JsonResponse({
        "unique_id": str(post.unique_id),
        "slug": post.slug,
        "status": post.status,
        "edit_url": reverse("post_edit", kwargs={"unique_id": post.unique_id}),
    })


def store_uploaded_image(user, uploaded):
    """图片上传的唯一实现：大小校验 → MD5 去重 → 转 WebP → 落库。

    返回与 ``/ajax/image/upload/`` 相同的字典（含 ``dedup`` 标记）；
    失败抛 ``ContentActionError``，Web 转 JSON、API 转统一错误体。
    """
    if uploaded is None:
        raise ContentActionError("未选择文件", 400, "no_file")
    if uploaded.size > MAX_UPLOAD_SIZE:
        raise ContentActionError("文件超过10MB限制", 400, "too_large")

    raw = uploaded.read()
    md5_hash, sha1_hash = _compute_hashes(raw)

    # Dedup: check for existing image with same MD5
    existing = UploadedImage.objects.filter(md5_hash=md5_hash).first()
    if existing:
        return {
            "url": existing.image.url,
            "name": existing.original_filename,
            "size": existing.file_size,
            "width": existing.width,
            "height": existing.height,
            "dedup": True,
        }

    try:
        webp_data, width, height = _process_image(raw)
    except Exception:
        raise ContentActionError("无法识别的图片文件", 400, "invalid_image")

    obj = UploadedImage(
        original_filename=uploaded.name,
        md5_hash=md5_hash,
        sha1_hash=sha1_hash,
        file_size=len(webp_data),
        width=width,
        height=height,
        uploader=user,
    )
    obj.image.save(f"{obj.id}.webp", ContentFile(webp_data), save=False)
    obj.save()

    return {
        "url": obj.image.url,
        "name": uploaded.name,
        "size": obj.file_size,
        "width": width,
        "height": height,
        "dedup": False,
    }


@require_POST
def image_upload_ajax(request):
    """Upload an image, process to WebP, deduplicate by hash, return JSON."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)
    try:
        payload = store_uploaded_image(request.user, request.FILES.get("image"))
    except ContentActionError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)
    return JsonResponse(payload)


# ── Safe Browsing URL check ──────────────────────────────────────────

@require_POST
def check_url_ajax(request):
    """Check a URL against Google Safe Browsing API. Returns JSON."""
    import json
    from urllib.request import Request, urlopen
    from urllib.error import URLError

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "无效请求"}, status=400)

    url_to_check = data.get("url", "").strip()
    if not url_to_check:
        return JsonResponse({"safe": True})  # no URL = skip

    api_key = getattr(settings, "GOOGLE_SAFE_BROWSING_KEY", "")
    if not api_key:
        return JsonResponse({"safe": True, "reason": "no-key"})

    payload = json.dumps({
        "client": {"clientId": "melicosmos", "clientVersion": "1.0.0"},
        "threatInfo": {
            "threatTypes": [
                "MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE",
                "POTENTIALLY_HARMFUL_APPLICATION", "THREAT_TYPE_UNSPECIFIED",
            ],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url_to_check}],
        },
    }).encode()

    try:
        req = Request(
            f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={api_key}",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=3) as resp:
            raw = resp.read()
            status = resp.status
    except Exception as exc:
        import logging
        logging.getLogger("melicosmos").warning("Safe Browsing network error: %s", exc)
        return JsonResponse({"safe": True, "reason": "api-error", "detail": str(exc)[:200]})

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return JsonResponse({"safe": True, "reason": "api-error", "detail": "Invalid JSON response"})

    if status != 200 or "error" in result:
        err = result.get("error", {}).get("message", f"HTTP {status}")
        import logging
        logging.getLogger("melicosmos").warning("Safe Browsing API error: %s", err)
        return JsonResponse({"safe": True, "reason": "api-error", "detail": err[:200]})

    threats = result.get("matches", [])
    threat_types = [m.get("threatType", "") for m in threats]
    return JsonResponse({
        "safe": len(threats) == 0,
        "threats": threat_types if threats else [],
    })


# ── Ping endpoint for client-side latency measurement ────────────────

def ping(request):
    """Return empty 204 — used by about page JS to measure RTT."""
    from django.http import HttpResponse
    return HttpResponse(status=204)


# ── Like / Comment ────────────────────────────────────────────────────

def _get_client_ip(request):
    """Extract client IP from request, handling proxy forwarding."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _rate_limit_check(key, limit, window=3600):
    """Simple cache-based rate limiter. Returns (allowed: bool, retry_seconds: int).

    窗口截止时间跟着计数一起存：只有 Redis 后端提供 ``cache.ttl()``，本项目用的是
    默认的 LocMemCache，原先在真正触到上限时调用它会抛 AttributeError，用户看到的
    是 500 而不是"请稍后再试"。缓存格式换了也只是重新计时，无持久化影响。
    """
    now = time.monotonic()
    entry = cache.get(key)
    if isinstance(entry, dict) and entry.get("until", 0) > now:
        remaining = max(int(entry["until"] - now) + 1, 1)
        count = int(entry.get("count", 0))
        if count >= limit:
            return False, remaining
        cache.set(key, {"count": count + 1, "until": entry["until"]}, remaining)
        return True, 0
    cache.set(key, {"count": 1, "until": now + window}, window)
    return True, 0


def _guest_is_trusted(email):
    """A guest is 'trusted' if they have at least one previously-approved visible comment."""
    return Comment.objects.filter(
        guest_email=email,
        user__isnull=True,
        is_visible=True,
    ).exists()


#: 允许点赞 / 评论的内容类型（``app_label.model``）
LIKABLE_MODELS = {"blog.post", "blog.memo"}


class ContentActionError(Exception):
    """点赞 / 评论等写操作的失败。

    Web 视图把它转成 messages（按 ``level``），API 把它转成对应状态码的 JSON。
    这样两端共用同一套规则，不会出现"移动端能绕过审核/限流"的情况。
    """

    def __init__(self, message, status=400, code=None, level="error", retry_after=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code or "action_failed"
        self.level = level
        #: 限流类错误带上"多少秒后可重试"，API 会写进 Retry-After 头
        self.retry_after = retry_after


def resolve_content_target(content_type_key, object_id, user, verb="评论"):
    """把 ``blog.post`` + pk 解析成 (ContentType, 对象)，并检查它对 user 是否可操作。

    只允许点赞/评论文章与碎碎念——避免把任意 ContentType 变成点赞对象。
    文案带句号：Web 端直接把 message 放进 toast，API 端按 ``code`` 分支。
    """
    if not content_type_key or not object_id:
        raise ContentActionError("参数错误。", 400, "missing_argument")
    try:
        app_label, model = content_type_key.split(".")
        ct = ContentType.objects.get(app_label=app_label, model=model)
        obj = ct.get_object_for_this_type(pk=object_id)
    except (ValueError, ContentType.DoesNotExist):
        raise ContentActionError("内容类型无效。", 400, "invalid_content_type")
    except ObjectDoesNotExist:
        raise ContentActionError("内容不存在。", 404, "not_found")

    if f"{ct.app_label}.{ct.model}" not in LIKABLE_MODELS:
        raise ContentActionError("内容类型无效。", 400, "invalid_content_type")

    is_owner = getattr(obj, "author", None) == user
    if hasattr(obj, "is_public") and not obj.is_public and not is_owner:
        raise ContentActionError(f"无法{verb}非公开内容。", 403, "forbidden")
    if hasattr(obj, "status") and obj.status != "published" and not is_owner:
        raise ContentActionError(f"无法{verb}未发布的内容。", 403, "forbidden")
    return ct, obj


def toggle_like(request, content_type_key, object_id):
    """点赞开关：返回 ``{liked, count, display_text}``，失败抛 ContentActionError。"""
    ct, obj = resolve_content_target(content_type_key, object_id, request.user, verb="点赞")

    like, created = Like.objects.get_or_create(
        user=request.user, content_type=ct, object_id=object_id,
    )
    if not created:
        like.delete()
    elif hasattr(obj, "author") and obj.author != request.user:
        actor_name = request.user.profile.display_name or request.user.username
        obj_title = getattr(obj, "title", None)
        if obj_title:
            message = f'{actor_name} 赞了你的文章《{obj_title}》'
        else:
            message = f'{actor_name} 赞了你的内容'
        Notification.objects.create(
            recipient=obj.author, actor=request.user, notification_type='like',
            message=message, content_type=ct, object_id=object_id,
        )

    state = get_like_state(ct, object_id, request.user)
    return {
        "liked": created,
        "count": state["count"],
        "display_text": state["display_text"],
    }


@require_POST
def like_toggle_ajax(request):
    """Toggle a like on any object (Post, Memo). Returns JSON."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)

    import json
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "无效请求"}, status=400)

    try:
        result = toggle_like(request, data.get("content_type"), data.get("object_id"))
    except ContentActionError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)
    return JsonResponse(result)


def create_comment(request, content_type_key, object_id, content,
                   guest_name="", guest_email=""):
    """写入评论的唯一实现：目标校验 → 限流 → 查重 → 落库 → 通知 → 游客头像。

    未登录即视为游客评论（首评要过审）。返回 ``Comment``，失败抛
    ``ContentActionError``；待审状态由调用方按 ``comment.is_visible`` 判断。
    """
    is_guest = not request.user.is_authenticated
    ct, content_object = resolve_content_target(content_type_key, object_id,
                                                 request.user, verb="评论")

    # Rate limiting
    comment_limit = getattr(settings, 'COMMENT_RATE_LIMIT', 5)
    if is_guest:
        rate_key = f"comment_rate:ip:{_get_client_ip(request)}"
    else:
        rate_key = f"comment_rate:user:{request.user.pk}"
    allowed, retry = _rate_limit_check(rate_key, comment_limit)
    if not allowed:
        minutes = max(1, retry // 60)
        raise ContentActionError(f"评论过于频繁，请 {minutes} 分钟后再试。",
                                429, "throttled", retry_after=retry)

    # Duplicate check: same content for same target within 30s
    dup_filter = {
        "content_type": ct,
        "object_id": object_id,
        "content": content,
        "created_time__gte": timezone.now() - timezone.timedelta(seconds=30),
    }
    if is_guest:
        dup_filter["guest_email"] = (guest_email or "").strip()
        dup_filter["user__isnull"] = True
    else:
        dup_filter["user"] = request.user
    if Comment.objects.filter(**dup_filter).exists():
        raise ContentActionError("请勿重复提交评论。", 409, "duplicate", level="warning")

    # Determine moderation status
    if is_guest:
        comment_user = None
        guest_name = (guest_name or "").strip()
        guest_email = (guest_email or "").strip()
        # First-time guest moderation
        is_visible = _guest_is_trusted(guest_email)
    else:
        comment_user = request.user
        guest_name = ""
        guest_email = ""
        is_visible = True

    comment = Comment.objects.create(
        user=comment_user,
        guest_name=guest_name,
        guest_email=guest_email,
        content_type=ct,
        object_id=object_id,
        content=content,
        is_visible=is_visible,
    )

    # Notification for content author (logged-in users only, don't notify self)
    if not is_guest and getattr(content_object, 'author', None) is not None \
            and content_object.author != request.user:
        actor_name = request.user.profile.display_name or request.user.username
        obj_title = getattr(content_object, 'title', None)
        if obj_title:
            notify_msg = f'{actor_name} 评论了你的文章《{obj_title}》'
        else:
            notify_msg = f'{actor_name} 评论了你的内容'
        Notification.objects.create(
            recipient=content_object.author,
            actor=request.user,
            notification_type='comment',
            message=notify_msg,
            content_type=ct,
            object_id=object_id,
        )

    # Generate local avatar for guest comment (idempotent, no-op for logged-in)
    if is_guest:
        get_or_create_guest_avatar(guest_email, guest_name)

    return comment


@require_POST
def comment_create(request):
    """Create a comment. Supports logged-in users and guest commenting."""
    next_url = request.POST.get("next") or "/"
    go = lambda: _safe_redirect(next_url, fragment='#comments')

    is_authenticated = request.user.is_authenticated

    form = CommentForm(request.POST, is_guest=not is_authenticated)
    if not form.is_valid():
        # Save form data + errors in session so the detail page can restore them
        request.session['comment_form_data'] = {
            k: v for k, v in request.POST.items()
            if k not in ('csrfmiddlewaretoken', 'next')
        }
        request.session['comment_form_errors'] = {
            f: [str(e) for e in errors]
            for f, errors in form.errors.items()
        }
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(request, f"评论失败：{err}")
        return go()

    # Honeypot check — bots fill hidden fields
    if form.cleaned_data.get("website"):
        messages.error(request, "评论失败，请重试。")
        return go()

    try:
        comment = create_comment(
            request,
            request.POST.get("content_type"),
            request.POST.get("object_id"),
            form.cleaned_data["content"],
            guest_name=form.cleaned_data.get("guest_name", ""),
            guest_email=form.cleaned_data.get("guest_email", ""),
        )
    except ContentActionError as exc:
        getattr(messages, exc.level)(request, exc.message)
        return go()

    response = go()
    if not comment.is_visible:
        # Save pending comment ID in signed cookie so the submitter can
        # always see it with "审核中" badge (persists across browser restarts)
        _add_pending_id_cookie(request, response, comment.pk)
        messages.success(request, "评论已提交，审核后将显示。")
    else:
        messages.success(request, "评论已发布。")
    return response


def comment_toggle_visibility(request, pk):
    """Toggle a comment's visibility. Staff only."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"error": "无权限"}, status=403)

    comment = get_object_or_404(Comment, pk=pk)
    comment.is_visible = not comment.is_visible
    comment.save(update_fields=['is_visible', 'modified_time'])

    return JsonResponse({
        "ok": True,
        "is_visible": comment.is_visible,
        "comment_id": comment.pk,
    })


class MemoDetailView(DetailView):
    """Individual memo page with likes and comments."""
    model = Memo
    template_name = "blog/memo_detail.html"
    context_object_name = "memo"

    def get_object(self, queryset=None):
        username = self.kwargs.get("username")
        pk = self.kwargs.get("pk")
        author = get_object_or_404(User, username=username)
        memo = get_object_or_404(
            Memo.objects.select_related("author", "author__profile"),
            pk=pk, author=author,
        )
        if not memo.is_public and memo.author != self.request.user:
            raise Http404("Memo not found")
        return memo

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        memo = self.object
        ct = ContentType.objects.get_for_model(Memo)

        # Likes
        state = get_like_state(ct, memo.pk, self.request.user)
        context["likes_count"] = state["count"]
        context["likes_users"] = state["names"]
        context["likes_display_text"] = state["display_text"]
        context["user_liked"] = state["user_liked"]

        # Comments (visible + user's own pending comment)
        comments = Comment.objects.filter(
            content_type=ct, object_id=memo.pk
        ).select_related("user__profile")

        # Show user's own pending comment (if any) with "审核中" badge
        pending_ids = _get_pending_ids_from_cookie(self.request)
        if pending_ids:
            pending = Comment.objects.filter(
                pk__in=pending_ids, content_type=ct, object_id=memo.pk, is_visible=False
            ).select_related("user__profile").first()
            if pending:
                context["pending_comment"] = pending

        context["comments"] = comments.filter(is_visible=True)

        # Recover form data from session (after validation error redirect)
        form_data = self.request.session.pop('comment_form_data', None)
        form_errors_session = self.request.session.pop('comment_form_errors', None)
        if form_data:
            context["comment_form"] = CommentForm(initial=form_data,
                                                  is_guest=not self.request.user.is_authenticated)
            if form_errors_session:
                context["form_errors_from_session"] = form_errors_session
        else:
            context["comment_form"] = CommentForm(is_guest=not self.request.user.is_authenticated)

        context["content_type_key"] = "blog.memo"
        context["object_id"] = memo.pk

        return context


# ═══════════════════════════════════════════════════════════════════════════
# Ban Appeal
# ═══════════════════════════════════════════════════════════════════════════

class BanAppealView(TemplateView):
    """Appeal form for banned users. One appeal per ban."""
    template_name = "blog/ban_appeal.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.profile.is_banned:
            messages.info(request, '您的账号目前没有被封禁。')
            return redirect('index')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        context['ban_reason'] = profile.banned_reason or '未提供原因'
        context['is_permanent'] = profile.is_permanent_ban
        context['banned_at'] = profile.banned_at
        context['ban_is_direct'] = not profile.banned_reason.startswith('上级用户')

        # Check for existing pending appeal
        context['existing_appeal'] = BanAppeal.objects.filter(
            user=self.request.user, is_resolved=False
        ).first()
        return context

    def post(self, request, *args, **kwargs):
        if not request.user.profile.is_banned:
            return redirect('index')

        existing = BanAppeal.objects.filter(user=request.user, is_resolved=False).first()
        if existing:
            messages.warning(request, '您已提交过申诉，请等待处理。')
            return self.render_to_response(self.get_context_data())

        content = request.POST.get('content', '').strip()
        if not content:
            messages.error(request, '申诉内容不能为空。')
            return self.render_to_response(self.get_context_data())

        BanAppeal.objects.create(user=request.user, content=content)
        messages.success(request, '申诉已提交，管理员将尽快处理。')
        return redirect('ban_appeal')


class InboxView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = "blog/inbox.html"
    context_object_name = "notifications"
    paginate_by = 20

    def get_queryset(self):
        return Notification.objects.filter(
            recipient=self.request.user
        ).select_related('actor__profile').order_by('-created_time')


def mark_notifications_read(user, pks=None, all_unread=False):
    """把收件箱通知标记为已读，返回更新条数。Web 的三个入口与 API 共用。"""
    qs = Notification.objects.filter(recipient=user, is_read=False)
    if not all_unread:
        qs = qs.filter(pk__in=pks or [])
    return qs.update(is_read=True)


def notification_read(request, pk):
    """Mark a notification as read and redirect to its target content."""
    if not request.user.is_authenticated:
        return redirect('login')
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    mark_notifications_read(request.user, pks=[pk])
    target_url = notification.get_target_url()
    if target_url:
        return redirect(target_url)
    return redirect('inbox')


@require_POST
def notification_mark_all_read(request):
    """Mark all of the current user's unread notifications as read."""
    if not request.user.is_authenticated:
        return redirect('login')
    mark_notifications_read(request.user, all_unread=True)
    return redirect('inbox')


@require_POST
def notification_mark_read(request, pk):
    """Mark a single notification as read without redirecting to its target."""
    if not request.user.is_authenticated:
        return redirect('login')
    get_object_or_404(Notification, pk=pk, recipient=request.user)
    mark_notifications_read(request.user, pks=[pk])
    return redirect('inbox')
