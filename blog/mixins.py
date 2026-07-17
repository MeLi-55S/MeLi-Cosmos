from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Q


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
