"""
MeLi Cosmos v2.0 → v3.0 - Blog Views.
Multi-user refactored Class-Based Views.
"""
from collections import OrderedDict
from datetime import date, timedelta

import hashlib
from io import BytesIO

import markdown as md_lib
from PIL import Image as PILImage

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.db.models import F, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.utils import timezone
from django.views.generic import (
    ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView,
)

from .forms import PostForm, MemoForm, SeriesForm, UserProfileForm
from .mixins import AuthorRequiredMixin, AuthorOrPublishedMixin, UserSpaceMixin
from .models import Post, Memo, Category, Tag, Series, UserProfile, InviteCode, UploadedImage, ViewLog


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
            ).order_by("-created_time")
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

        md_extensions = getattr(
            settings, "MARKDOWN_EXTENSIONS", ["extra", "codehilite", "fenced_code"],
        )
        md = md_lib.Markdown(extensions=md_extensions)
        post.body_html = md.convert(post.body)
        post.toc = md.toc

        char_count = len(post.body)
        read_time = max(1, round(char_count / 1000))
        context["read_time"] = read_time

        # Series navigation
        if post.series:
            series_posts = list(Post.objects.filter(
                series=post.series, status="published"
            ).select_related("author").order_by("series_order", "created_time").only("id", "slug", "title", "series_order", "author__username"))
            for i, sp in enumerate(series_posts):
                if sp.pk == post.pk:
                    if i > 0:
                        context["series_prev"] = series_posts[i - 1]
                    if i < len(series_posts) - 1:
                        context["series_next"] = series_posts[i + 1]
                    context["series_name"] = post.series.name
                    context["series_slug"] = post.series.slug
                    context["series_index"] = i + 1
                    context["series_total"] = len(series_posts)
                    break

        # Related posts
        tag_ids = post.tags.values_list("id", flat=True)
        if tag_ids:
            context["related_posts"] = Post.objects.filter(
                status="published", tags__in=tag_ids
            ).exclude(pk=post.pk).distinct().order_by("-created_time")[:3]

        return context


# ═══════════════════════════════════════════════════════════════════════════
# Post CRUD
# ═══════════════════════════════════════════════════════════════════════════

class PostCreateView(LoginRequiredMixin, CreateView):
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
        post.save(update_fields=["status"])
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
        if next_url:
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
            ).prefetch_related("post_set")
        return Series.objects.prefetch_related("post_set").all()

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
        else:
            self.tag = get_object_or_404(Tag, slug=slug)

        return Post.objects.filter(
            status="published", tags=self.tag
        ).select_related("category", "author", "author__profile").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_filter"] = f"标签：{self.tag.name}"
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
            self.template_name = "blog/includes/user_layout.html"
        else:
            self.series = get_object_or_404(Series, slug=slug)
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
            import markdown as md_lib
            from django.conf import settings
            context["current_post_body_html"] = md_lib.markdown(
                current_post.body,
                extensions=settings.MARKDOWN_EXTENSIONS,
            )
            # Find index and prev/next
            try:
                idx = posts.index(current_post)
            except ValueError:
                idx = 0
            context["current_index"] = idx + 1
            context["prev_post"] = posts[idx - 1] if idx > 0 else None
            context["next_post"] = posts[idx + 1] if idx < len(posts) - 1 else None

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


# ═══════════════════════════════════════════════════════════════════════════
# Auth: Invite & Registration
# ═══════════════════════════════════════════════════════════════════════════

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
        # Frequency check
        if not request.user.is_staff:
            today_unused = InviteCode.objects.filter(
                inviter=request.user,
                created_at__date=date.today(),
                is_used=False,
            ).count()
            if today_unused >= getattr(settings, "INVITE_DAILY_LIMIT", 1):
                messages.error(request, "今日邀请码生成已达上限（1个/天），请明天再试。")
                return redirect("invite")

        expire_hours = getattr(settings, "INVITE_CODE_EXPIRE_HOURS", 24)
        code = InviteCode.objects.create(
            code=InviteCode.generate_code(),
            inviter=request.user,
            expires_at=timezone.now() + timedelta(hours=expire_hours),
        )
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
        # Mark invite code as used
        self.invite_code.is_used = True
        self.invite_code.invitee = user
        self.invite_code.used_at = timezone.now()
        self.invite_code.save()
        # Auto-login
        login(self.request, user)
        messages.success(self.request, f"欢迎加入 MeLi Cosmos，{user.username}！")
        # Respect next parameter, fallback to profile setup. Skip landing page.
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and next_url != "/" and next_url != "":
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


@require_POST
def avatar_upload_ajax(request):
    """Receive cropped avatar canvas data, process, save to profile."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)

    uploaded = request.FILES.get("image")
    if not uploaded:
        return JsonResponse({"error": "未选择文件"}, status=400)

    if uploaded.size > MAX_UPLOAD_SIZE:
        return JsonResponse({"error": "文件超过10MB限制"}, status=400)

    raw = uploaded.read()
    try:
        webp_data, w, h = _process_image(raw)
    except Exception:
        return JsonResponse({"error": "无法识别的图片文件"}, status=400)

    profile = request.user.profile
    if profile.avatar:
        profile.avatar.delete(save=False)
    profile.avatar.save(
        f"{request.user.username}.webp",
        ContentFile(webp_data),
        save=True,
    )

    return JsonResponse({
        "url": profile.avatar.url,
        "width": w,
        "height": h,
    })


class InviteCodeEntryView(TemplateView):
    """Landing page for entering an invite code manually."""
    template_name = "blog/register_entry.html"

    def post(self, request, *args, **kwargs):
        code = request.POST.get("code", "").strip()
        if not code:
            messages.error(request, "请输入邀请码。")
            return self.render_to_response(self.get_context_data())
        try:
            invite = InviteCode.objects.get(code=code)
            if invite.is_used:
                messages.error(request, "此邀请码已被使用。")
                return self.render_to_response(self.get_context_data())
            if invite.is_expired:
                messages.error(request, "此邀请码已过期。")
                return self.render_to_response(self.get_context_data())
            next_url = request.POST.get("next", "")
            url = reverse("register", kwargs={"code": code})
            if next_url:
                url += "?next=" + next_url
            return redirect(url)
        except InviteCode.DoesNotExist:
            messages.error(request, "无效的邀请码，请检查后重试。")
            return self.render_to_response(self.get_context_data())


# ── View Counting ─────────────────────────────────────────────────────

@require_POST
@csrf_exempt
def view_count_ajax(request):
    """Record a post view based on browser fingerprint + IP hash + session."""
    import json
    from datetime import timedelta

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"counted": False}, status=400)

    post_id = data.get("post_id")
    fingerprint = data.get("fingerprint", "")

    if not post_id or not fingerprint:
        return JsonResponse({"counted": False}, status=400)

    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        ip = x_forwarded.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "")

    salt = settings.SECRET_KEY
    ip_hash = hashlib.sha256((salt + ip).encode()).hexdigest()

    # Secondary: session check (AND logic — both must pass)
    session_key = f"post_viewed_{post_id}"
    if request.session.get(session_key):
        return JsonResponse({"counted": False})

    # Primary: fingerprint OR IP cooldown check (either match → block)
    cooldown_hours = getattr(settings, "VIEW_LOG_COOLDOWN_HOURS", 1)
    cutoff = timezone.now() - timedelta(hours=cooldown_hours)

    if ViewLog.objects.filter(post_id=post_id, created_at__gte=cutoff).filter(
        Q(fingerprint_hash=fingerprint) | Q(ip_hash=ip_hash)
    ).exists():
        return JsonResponse({"counted": False})

    Post.objects.filter(pk=post_id).update(views=F("views") + 1)
    ViewLog.objects.create(
        post_id=post_id,
        fingerprint_hash=fingerprint,
        ip_hash=ip_hash,
    )
    request.session[session_key] = True

    return JsonResponse({"counted": True})


# ── AJAX helpers ──────────────────────────────────────────────────────

@require_POST
def category_create_ajax(request):
    """Create a new Category for the current user via AJAX."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "分类名不能为空"}, status=400)
    from .models import Category
    from django.utils.text import slugify
    slug = slugify(name, allow_unicode=True)
    cat, created = Category.objects.get_or_create(
        slug=slug, author=request.user, defaults={"name": name}
    )
    return JsonResponse({"id": cat.pk, "name": cat.name, "slug": cat.slug, "created": created})


@require_POST
def series_create_ajax(request):
    """Create a new Series for the current user via AJAX."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "系列名不能为空"}, status=400)
    from .models import Series
    from django.utils.text import slugify
    slug = slugify(name, allow_unicode=True)
    ser, created = Series.objects.get_or_create(
        slug=slug, author=request.user, defaults={"name": name}
    )
    return JsonResponse({"id": ser.pk, "name": ser.name, "slug": ser.slug, "created": created})


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
def image_upload_ajax(request):
    """Upload an image, process to WebP, deduplicate by hash, return JSON."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "请先登录"}, status=403)

    uploaded = request.FILES.get("image")
    if not uploaded:
        return JsonResponse({"error": "未选择文件"}, status=400)

    if uploaded.size > MAX_UPLOAD_SIZE:
        return JsonResponse({"error": "文件超过10MB限制"}, status=400)

    raw = uploaded.read()
    md5_hash, sha1_hash = _compute_hashes(raw)

    # Dedup: check for existing image with same MD5
    existing = UploadedImage.objects.filter(md5_hash=md5_hash).first()
    if existing:
        return JsonResponse({
            "url": existing.image.url,
            "name": existing.original_filename,
            "size": existing.file_size,
            "width": existing.width,
            "height": existing.height,
            "dedup": True,
        })

    # Process image
    try:
        webp_data, width, height = _process_image(raw)
    except Exception:
        return JsonResponse({"error": "无法识别的图片文件"}, status=400)

    obj = UploadedImage(
        original_filename=uploaded.name,
        md5_hash=md5_hash,
        sha1_hash=sha1_hash,
        file_size=len(webp_data),
        width=width,
        height=height,
        uploader=request.user,
    )
    obj.image.save(f"{obj.id}.webp", ContentFile(webp_data), save=False)
    obj.save()

    return JsonResponse({
        "url": obj.image.url,
        "name": uploaded.name,
        "size": obj.file_size,
        "width": width,
        "height": height,
        "dedup": False,
    })
