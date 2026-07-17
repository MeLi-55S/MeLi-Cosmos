from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.models import User
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404

from .models import Post, Memo


class AuthorRequiredMixin(UserPassesTestMixin):
    """Allow access only to the object's author."""

    def test_func(self):
        obj = self.get_object()
        return obj.author == self.request.user


class AuthorOrPublishedMixin:
    """Queryset: published posts for all, plus own content for authenticated users."""

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated:
            return qs.filter(Q(status='published') | Q(author=user))
        return qs.filter(status='published')


class UserSpaceMixin:
    """Shared context for user-space pages under /@<username>/.

    Injects space_owner, space_profile, space_stats, and active_tab.
    Sets template_name to the shared user_layout.html.
    """

    active_tab = "posts"  # override in subclasses
    content_template = "blog/includes/user_posts.html"  # override in subclasses

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        username = self.kwargs.get("username")
        if username:
            self.space_owner = get_object_or_404(
                User.objects.select_related("profile"), username=username
            )
            self.space_profile = self.space_owner.profile

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if hasattr(self, "space_owner"):
            context["space_owner"] = self.space_owner
            context["space_profile"] = self.space_profile
            context["space_stats"] = self._get_space_stats()
            context["active_tab"] = self.active_tab
            context["content_template"] = self.content_template
        return context

    def _get_space_stats(self):
        owner = self.space_owner
        return {
            "total_posts": Post.objects.filter(author=owner).count(),
            "published_count": Post.objects.filter(author=owner, status="published").count(),
            "total_views": Post.objects.filter(
                author=owner, status="published"
            ).aggregate(total=Sum("views"))["total"] or 0,
            "latest_memo": Memo.objects.filter(author=owner, is_public=True).first(),
        }
