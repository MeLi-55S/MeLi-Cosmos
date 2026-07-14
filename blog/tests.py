"""
Tests for Project MyCosmos blog app.
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User

from .models import Category, Tag, Post, Memo


class CategoryModelTests(TestCase):
    def test_slug_auto_generated(self):
        cat = Category.objects.create(name="测试分类")
        self.assertEqual(cat.slug, "测试分类")

    def test_str_returns_name(self):
        cat = Category.objects.create(name="测试分类")
        self.assertEqual(str(cat), "测试分类")


class TagModelTests(TestCase):
    def test_slug_auto_generated(self):
        tag = Tag.objects.create(name="测试标签")
        self.assertEqual(tag.slug, "测试标签")

    def test_str_returns_name(self):
        tag = Tag.objects.create(name="测试标签")
        self.assertEqual(str(tag), "测试标签")


class PostModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="foo", password="bar")

    def test_excerpt_auto_generated(self):
        post = Post.objects.create(
            title="Test",
            author=self.user,
            body="A" * 200,
            status="published",
        )
        self.assertTrue(post.excerpt.startswith("A" * 150))
        self.assertTrue(post.excerpt.endswith("..."))

    def test_unique_id_auto_populated(self):
        post = Post.objects.create(title="U", author=self.user)
        self.assertIsNotNone(post.unique_id)

    def test_default_status_is_draft(self):
        post = Post.objects.create(title="D", author=self.user)
        self.assertEqual(post.status, "draft")


class ViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="bar", password="foo")
        self.cat = Category.objects.create(name="Dev")
        self.tag = Tag.objects.create(name="Python")
        self.post = Post.objects.create(
            title="Hello World",
            body="Lorem ipsum " * 100,
            author=self.user,
            category=self.cat,
            status="published",
        )
        self.post.tags.add(self.tag)

    def test_index_200(self):
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello World")

    def test_post_detail_by_slug_200(self):
        resp = self.client.get(reverse("post_detail", kwargs={"slug": self.post.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello World")

    def test_post_detail_by_pk_200(self):
        resp = self.client.get(reverse("post_detail", kwargs={"slug": str(self.post.pk)}))
        self.assertEqual(resp.status_code, 200)

    def test_post_by_tag_200(self):
        resp = self.client.get(reverse("post_by_tag", kwargs={"slug": self.tag.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello World")

    def test_post_by_category_200(self):
        resp = self.client.get(reverse("post_by_category", kwargs={"slug": self.cat.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello World")

    def test_archives_200(self):
        resp = self.client.get(reverse("archives"))
        self.assertEqual(resp.status_code, 200)

    def test_about_200(self):
        resp = self.client.get(reverse("about"))
        self.assertEqual(resp.status_code, 200)

    def test_memo_list_200(self):
        resp = self.client.get(reverse("memo_list"))
        self.assertEqual(resp.status_code, 200)

    def test_rss_feed_200(self):
        resp = self.client.get(reverse("rss_feed"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello World")

    def test_search_200(self):
        resp = self.client.get(reverse("search"), {"q": "Hello"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello World")

    def test_search_empty_query(self):
        resp = self.client.get(reverse("search"), {"q": ""})
        self.assertEqual(resp.status_code, 200)

    def test_draft_post_hidden_from_public(self):
        draft = Post.objects.create(
            title="Secret Draft",
            author=self.user,
            status="draft",
        )
        resp = self.client.get(reverse("post_detail", kwargs={"slug": draft.slug}))
        self.assertEqual(resp.status_code, 404)

        # Index should not show draft
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Secret Draft")


class AuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.author = User.objects.create_user(username="author", password="pw")
        self.other = User.objects.create_user(username="other", password="pw")
        self.post = Post.objects.create(
            title="Author's Post",
            author=self.author,
            status="published",
        )

    def test_unauthenticated_cannot_create(self):
        resp = self.client.get(reverse("post_create"))
        self.assertEqual(resp.status_code, 302)  # redirects to login

    def test_non_author_cannot_edit(self):
        self.client.login(username="other", password="pw")
        resp = self.client.get(reverse("post_edit", kwargs={"unique_id": self.post.unique_id}))
        self.assertEqual(resp.status_code, 403)

    def test_non_author_cannot_delete(self):
        self.client.login(username="other", password="pw")
        resp = self.client.get(reverse("post_delete", kwargs={"unique_id": self.post.unique_id}))
        self.assertEqual(resp.status_code, 403)

    def test_author_can_edit_own_post(self):
        self.client.login(username="author", password="pw")
        resp = self.client.get(reverse("post_edit", kwargs={"unique_id": self.post.unique_id}))
        self.assertEqual(resp.status_code, 200)

    def test_author_can_view_own_draft(self):
        self.client.login(username="author", password="pw")
        draft = Post.objects.create(title="My Draft", author=self.author, status="draft")
        resp = self.client.get(reverse("post_detail", kwargs={"slug": draft.slug}))
        self.assertEqual(resp.status_code, 200)


class MemoTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.memo = Memo.objects.create(content="Public memo", is_public=True)
        Memo.objects.create(content="Private memo", is_public=False)

    def test_public_memo_visible(self):
        resp = self.client.get(reverse("memo_list"))
        self.assertContains(resp, "Public memo")
        self.assertNotContains(resp, "Private memo")

    def test_private_memo_visible_to_authenticated(self):
        user = User.objects.create_user(username="u", password="p")
        self.client.login(username="u", password="p")
        resp = self.client.get(reverse("memo_list"))
        self.assertContains(resp, "Public memo")
        self.assertContains(resp, "Private memo")


class SitemapTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="s", password="s")
        Post.objects.create(
            title="Published",
            author=self.user,
            status="published",
            body="Test content",
        )

    def test_sitemap_200(self):
        resp = self.client.get("/sitemap.xml")
        self.assertEqual(resp.status_code, 200)
