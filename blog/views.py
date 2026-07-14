"""
Project MyCosmos v2.0 - Blog Views.
Class-Based Views (CBVs) for high-performance rendering.
"""
from collections import OrderedDict

import markdown as md_lib

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import (
    ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView,
)

from .forms import PostForm, MemoForm, SeriesForm
from .models import Post, Memo, Category, Tag, Series


class IndexView(ListView):
    """Home page: latest published Posts + latest public Memo."""
    model = Post
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 10

    def get_queryset(self):
        qs = Post.objects.select_related("category", "author").prefetch_related("tags")
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
    """Search posts by keyword in title and body."""
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
            ).select_related("category", "author").prefetch_related("tags")
        return Post.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_filter"] = f'Search: "{self.request.GET.get("q", "")}"'
        return context


class PostDetailView(DetailView):
    """Article detail: post by slug, increment views with session protection."""
    model = Post
    template_name = "blog/post_detail.html"
    context_object_name = "post"

    def get_object(self, queryset=None):
        slug_or_pk = self.kwargs.get("slug")
        qs = Post.objects.select_related("category", "author").prefetch_related("tags")

        # Try pk first (for pure numeric slugs like old permalink IDs),
        # fall back to slug lookup
        try:
            pk = int(slug_or_pk)
            post = qs.filter(pk=pk).first()
            if post is not None:
                # Redirect to canonical slug URL for SEO
                return post
        except (ValueError, TypeError):
            pass

        post = get_object_or_404(qs, slug=slug_or_pk)

        # Draft/private posts are only visible to their author
        if post.status != "published" and post.author != self.request.user:
            from django.http import Http404
            raise Http404("Post not found")
        return post

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        post = self.object

        # Server-side Markdown -> HTML rendering with code highlighting + TOC
        md_extensions = getattr(
            settings,
            "MARKDOWN_EXTENSIONS",
            ["extra", "codehilite", "fenced_code"],
        )
        md = md_lib.Markdown(extensions=md_extensions)
        post.body_html = md.convert(post.body)
        post.toc = md.toc

        # Read time estimation
        char_count = len(post.body)
        read_time = max(1, round(char_count / 1000))
        context["read_time"] = read_time

        # Series navigation (prev/next within same series)
        if post.series:
            series_posts = list(Post.objects.filter(
                series=post.series, status="published"
            ).order_by("series_order", "-created_time").only("id", "slug", "title", "series_order"))
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

        # Related posts by shared tags
        tag_ids = post.tags.values_list("id", flat=True)
        if tag_ids:
            context["related_posts"] = Post.objects.filter(
                status="published", tags__in=tag_ids
            ).exclude(pk=post.pk).distinct().order_by("-created_time")[:3]

        # Session-based view counting (anti-F5 bloating protection)
        session_key = f"post_viewed_{post.pk}"
        if not self.request.session.get(session_key):
            Post.objects.filter(pk=post.pk).update(views=F("views") + 1)
            self.request.session[session_key] = True

        return context


# =============================================================================
# Article Publishing (CRUD)
# =============================================================================

class PostCreateView(LoginRequiredMixin, CreateView):
    model = Post
    form_class = PostForm
    template_name = "blog/post_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["existing_tag_names"] = list(
            Tag.objects.values_list("name", flat=True).order_by("name")
        )
        return context

    def form_valid(self, form):
        form.instance.author = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("post_detail", kwargs={"slug": self.object.slug})


class PostUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Post
    form_class = PostForm
    template_name = "blog/post_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["existing_tag_names"] = list(
            Tag.objects.values_list("name", flat=True).order_by("name")
        )
        return context

    def get_object(self, queryset=None):
        return get_object_or_404(Post, unique_id=self.kwargs["unique_id"])

    def test_func(self):
        return self.request.user == self.get_object().author

    def get_success_url(self):
        return reverse("post_detail", kwargs={"slug": self.object.slug})


class PostDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Post
    template_name = "blog/post_confirm_delete.html"
    context_object_name = "post"

    def get_object(self, queryset=None):
        return get_object_or_404(Post, unique_id=self.kwargs["unique_id"])

    def test_func(self):
        return self.request.user == self.get_object().author

    def get_success_url(self):
        return reverse("index")


class PostPublishView(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    """Publish a draft post via POST."""
    model = Post
    http_method_names = ["post"]

    def get_object(self, queryset=None):
        return get_object_or_404(Post, unique_id=self.kwargs["unique_id"])

    def test_func(self):
        return self.request.user == self.get_object().author

    def post(self, request, *args, **kwargs):
        post = self.get_object()
        post.status = "published"
        post.save(update_fields=["status"])
        return redirect(reverse("post_detail", kwargs={"slug": post.slug}))


# =============================================================================
# Memo (Treehole / Status)
# =============================================================================

class MemoListView(ListView):
    model = Memo
    template_name = "blog/memo_list.html"
    context_object_name = "memos"
    paginate_by = 20

    def get_queryset(self):
        qs = Memo.objects.all()
        if not self.request.user.is_authenticated:
            qs = qs.filter(is_public=True)
        return qs


class MemoCreateView(LoginRequiredMixin, CreateView):
    model = Memo
    form_class = MemoForm
    template_name = "blog/memo_form.html"

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url:
            return next_url
        return reverse("memo_list")


# =============================================================================
# Navigation Pages
# =============================================================================

class ArchivesView(ListView):
    model = Post
    template_name = "blog/archives.html"
    context_object_name = "posts"

    def get_queryset(self):
        return Post.objects.filter(status="published").select_related(
            "category"
        ).only("title", "slug", "created_time", "category__name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        archives = OrderedDict()
        for post in context["posts"]:
            key = (post.created_time.year, post.created_time.month)
            archives.setdefault(key, []).append(post)
        context["archives"] = archives
        return context


class AboutView(TemplateView):
    template_name = "blog/about.html"


class SeriesCreateView(LoginRequiredMixin, CreateView):
    model = Series
    form_class = SeriesForm
    template_name = "blog/series_form.html"

    def get_success_url(self):
        return reverse("series_list")


class SeriesListView(ListView):
    """List all series with post counts."""
    model = Series
    template_name = "blog/series_list.html"
    context_object_name = "series_list"

    def get_queryset(self):
        return Series.objects.prefetch_related("post_set").all()


class PostByTagView(ListView):
    model = Post
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 10

    def get_queryset(self):
        self.tag = get_object_or_404(Tag, slug=self.kwargs["slug"])
        return Post.objects.filter(
            status="published", tags=self.tag
        ).select_related("category", "author").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_filter"] = f"#{self.tag.name}"
        return context


class PostBySeriesView(ListView):
    """List published posts within a series."""
    model = Post
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 10

    def get_queryset(self):
        self.series = get_object_or_404(Series, slug=self.kwargs["slug"])
        return Post.objects.filter(
            status="published", series=self.series
        ).select_related("category", "author", "series").prefetch_related("tags").order_by("series_order", "-created_time")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_filter"] = f"Series: {self.series.name}"
        return context


class PostByCategoryView(ListView):
    model = Post
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 10

    def get_queryset(self):
        self.category = get_object_or_404(Category, slug=self.kwargs["slug"])
        return Post.objects.filter(
            status="published", category=self.category
        ).select_related("category", "author").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_filter"] = self.category.name
        return context
