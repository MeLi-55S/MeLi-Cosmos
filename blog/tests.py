"""
Tests for MeLi Cosmos blog app (multi-user).
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from .models import Category, Tag, Post, Memo, Series, UserProfile, InviteCode, UploadedImage, Notification
from . import models as blog_models

# ── 测试期间跳过 DiceBear 头像下载（避免 HTTP 超时拖慢测试）──
_original_generate = blog_models.generate_default_avatar


def _mock_generate(profile):
    """Skip DiceBear download in tests, just create an empty placeholder."""
    from django.core.files.base import ContentFile
    profile.avatar.save(f"{profile.user.username}.webp", ContentFile(b""), save=True)
    return True


blog_models.generate_default_avatar = _mock_generate


class ModelTests(TestCase):
    """Category / Tag / Post / UserProfile / InviteCode model tests."""

    def setUp(self):
        self.user = User.objects.create_user(username="model_test_user", password="pw")
        self.client = Client()

    # ── Category ───────────────────────────────────────────────────

    def test_category_slug_auto_generated(self):
        cat = Category.objects.create(name="测试分类", author=self.user)
        self.assertEqual(cat.slug, "测试分类")

    def test_category_str_returns_name(self):
        cat = Category.objects.create(name="测试分类", author=self.user)
        self.assertEqual(str(cat), "测试分类")

    def test_category_unique_per_author(self):
        user2 = User.objects.create_user(username="other_cat", password="pw")
        Category.objects.create(name="Dev", author=self.user)
        Category.objects.create(name="Dev", author=user2)
        self.assertEqual(Category.objects.filter(slug="dev").count(), 2)

    # ── Tag ────────────────────────────────────────────────────────

    def test_tag_slug_auto_generated(self):
        tag = Tag.objects.create(name="测试标签", author=self.user)
        self.assertEqual(tag.slug, "测试标签")

    def test_tag_str_returns_name(self):
        tag = Tag.objects.create(name="测试标签", author=self.user)
        self.assertEqual(str(tag), "测试标签")

    # ── Post ───────────────────────────────────────────────────────

    def test_post_excerpt_auto_generated(self):
        post = Post.objects.create(title="Test", author=self.user, body="A" * 200, status="published")
        self.assertTrue(post.excerpt.startswith("A" * 150))
        self.assertTrue(post.excerpt.endswith("..."))

    def test_post_unique_id_auto_populated(self):
        post = Post.objects.create(title="U", author=self.user)
        self.assertIsNotNone(post.unique_id)

    def test_post_default_status_is_published(self):
        post = Post.objects.create(title="D", author=self.user)
        self.assertEqual(post.status, "published")

    # ── UserProfile ────────────────────────────────────────────────

    def test_profile_auto_created_on_user_creation(self):
        u = User.objects.create_user(username="new_user", password="pw")
        self.assertTrue(hasattr(u, "profile"))
        self.assertIsInstance(u.profile, UserProfile)
        self.assertEqual(u.profile.display_name, "new_user")

    # ── InviteCode ─────────────────────────────────────────────────

    def test_invite_generate_code(self):
        code = InviteCode.generate_code()
        self.assertEqual(len(code), 32)

    def test_invite_code_expiration(self):
        code = InviteCode.objects.create(
            code=InviteCode.generate_code(), inviter=self.user,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        self.assertFalse(code.is_expired)
        expired = InviteCode.objects.create(
            code=InviteCode.generate_code(), inviter=self.user,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(expired.is_expired)

    def test_invite_daily_limit(self):
        self.client.login(username="model_test_user", password="pw")
        InviteCode.objects.create(
            code=InviteCode.generate_code(), inviter=self.user,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        resp = self.client.get(reverse("invite"))
        self.assertFalse(resp.context["can_generate"])

    def test_invite_staff_no_limit(self):
        staff = User.objects.create_user(username="staff", password="pw", is_staff=True)
        self.client.login(username="staff", password="pw")
        InviteCode.objects.create(
            code=InviteCode.generate_code(), inviter=staff,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        resp = self.client.get(reverse("invite"))
        self.assertTrue(resp.context["can_generate"])

    def test_invite_generate_post(self):
        self.client.login(username="model_test_user", password="pw")
        resp = self.client.post(reverse("invite"))
        self.assertRedirects(resp, reverse("invite"))
        self.assertEqual(InviteCode.objects.filter(inviter=self.user).count(), 1)


class ViewTests(TestCase):
    """View / auth / visibility / memo tests — shared DB setup."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="alice", password="pw")
        self.user_b = User.objects.create_user(username="bob", password="pw")
        self.cat = Category.objects.create(name="Dev", author=self.user)
        self.tag = Tag.objects.create(name="Python", author=self.user)
        self.post = Post.objects.create(
            title="Alice Post", body="Lorem ipsum " * 100,
            author=self.user, category=self.cat, status="published",
        )
        self.post.tags.add(self.tag)
        self.draft = Post.objects.create(title="Alice Draft", author=self.user, status="draft")
        Post.objects.create(title="Bob Post", author=self.user_b, status="published")
        Memo.objects.create(content="Alice memo", is_public=True, author=self.user)
        Memo.objects.create(content="Alice private memo", is_public=False, author=self.user)
        Memo.objects.create(content="Bob memo", is_public=True, author=self.user_b)

    # ── Page 200s ──────────────────────────────────────────────────

    def test_index_200(self):
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice Post")

    def test_post_detail_by_slug_200(self):
        resp = self.client.get(reverse("post_detail", kwargs={"username": "alice", "slug": self.post.slug}))
        self.assertEqual(resp.status_code, 200)

    def test_post_by_tag_200(self):
        resp = self.client.get(reverse("post_by_tag", kwargs={"slug": self.tag.slug}))
        self.assertEqual(resp.status_code, 200)

    def test_post_by_category_200(self):
        resp = self.client.get(reverse("post_by_category", kwargs={"slug": self.cat.slug}))
        self.assertEqual(resp.status_code, 200)

    def test_archives_200(self):
        self.assertEqual(self.client.get(reverse("archives")).status_code, 200)

    def test_about_200(self):
        self.assertEqual(self.client.get(reverse("about")).status_code, 200)

    def test_memo_list_200(self):
        self.assertEqual(self.client.get(reverse("memo_list")).status_code, 200)

    def test_rss_feed_200(self):
        resp = self.client.get(reverse("rss_feed"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice Post")

    def test_search_200(self):
        resp = self.client.get(reverse("search"), {"q": "Lorem"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice Post")

    def test_search_empty_query(self):
        self.assertEqual(self.client.get(reverse("search"), {"q": ""}).status_code, 200)

    def test_user_space_200(self):
        resp = self.client.get(reverse("user_space", kwargs={"username": "alice"}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice Post")

    def test_legacy_post_detail(self):
        resp = self.client.get(reverse("post_detail_legacy", kwargs={"slug": self.post.slug}))
        self.assertEqual(resp.status_code, 200)

    def test_sitemap_200(self):
        self.assertEqual(self.client.get("/sitemap.xml").status_code, 200)

    # ── Draft visibility ───────────────────────────────────────────

    def test_draft_hidden_from_public(self):
        resp = self.client.get(reverse("post_detail", kwargs={"username": "alice", "slug": self.draft.slug}))
        self.assertEqual(resp.status_code, 404)
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Alice Draft")

    def test_user_b_cannot_see_user_a_draft(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("post_detail", kwargs={"username": "alice", "slug": self.draft.slug}))
        self.assertEqual(resp.status_code, 404)

    def test_user_a_sees_own_draft_on_index(self):
        self.client.login(username="alice", password="pw")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Alice Draft")

    def test_user_b_does_not_see_alice_draft_on_index(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Alice Draft")

    def test_user_space_shows_only_author_posts(self):
        resp = self.client.get(reverse("user_space", kwargs={"username": "alice"}))
        self.assertContains(resp, "Alice Post")
        self.assertNotContains(resp, "Bob Post")

    def test_user_space_hides_drafts_from_others(self):
        resp = self.client.get(reverse("user_space", kwargs={"username": "alice"}))
        self.assertNotContains(resp, "Alice Draft")

    # ── Category / Tag isolation ───────────────────────────────────

    def test_same_slug_different_authors(self):
        Category.objects.create(name="Python", author=self.user)
        Category.objects.create(name="Python", author=self.user_b)
        self.assertEqual(Category.objects.filter(slug="python").count(), 2)

    def test_user_category_shows_own_posts(self):
        resp = self.client.get(reverse("user_post_by_category", kwargs={"username": "alice", "slug": self.cat.slug}))
        self.assertContains(resp, "Alice Post")

    # ── Memo visibility ────────────────────────────────────────────

    def test_global_memo_list_shows_all_public(self):
        resp = self.client.get(reverse("memo_list"))
        self.assertContains(resp, "Alice memo")
        self.assertContains(resp, "Bob memo")
        self.assertNotContains(resp, "Alice private memo")

    def test_user_memo_list_shows_only_author(self):
        resp = self.client.get(reverse("user_memo_list", kwargs={"username": "alice"}))
        self.assertContains(resp, "Alice memo")
        self.assertNotContains(resp, "Bob memo")

    def test_user_memo_list_shows_private_to_author(self):
        self.client.login(username="alice", password="pw")
        resp = self.client.get(reverse("user_memo_list", kwargs={"username": "alice"}))
        self.assertContains(resp, "Alice private memo")

    def test_memo_private_visible_to_authenticated(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("memo_list"))
        self.assertContains(resp, "Alice memo")
        self.assertContains(resp, "Alice private memo")

    # ── Auth ───────────────────────────────────────────────────────

    def test_auth_unauthenticated_cannot_create(self):
        resp = self.client.get(reverse("post_create"))
        self.assertEqual(resp.status_code, 302)

    def test_auth_non_author_cannot_edit(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("post_edit", kwargs={"unique_id": self.post.unique_id}))
        self.assertEqual(resp.status_code, 403)

    def test_auth_non_author_cannot_delete(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("post_delete", kwargs={"unique_id": self.post.unique_id}))
        self.assertEqual(resp.status_code, 403)

    def test_auth_author_can_edit_own_post(self):
        self.client.login(username="alice", password="pw")
        resp = self.client.get(reverse("post_edit", kwargs={"unique_id": self.post.unique_id}))
        self.assertEqual(resp.status_code, 200)

    def test_auth_author_can_view_own_draft(self):
        self.client.login(username="alice", password="pw")
        resp = self.client.get(reverse("post_detail", kwargs={"username": "alice", "slug": self.draft.slug}))
        self.assertEqual(resp.status_code, 200)


class ThemeSystemTests(TestCase):
    """验证主题切换系统的渲染正确性。"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="theme_tester", password="pw")
        self.client.login(username="theme_tester", password="pw")

    def _get_html(self):
        return self.client.get(reverse("index")).content.decode()

    # ── HTML 结构 ──────────────────────────────────────────────

    def test_html_tag_has_dark_class(self):
        html = self._get_html()
        self.assertIn('<html lang="zh-CN" class="dark">', html)

    def test_tailwind_css_is_compiled(self):
        import os
        from django.conf import settings
        css_path = os.path.join(settings.BASE_DIR, 'static', 'css', 'tailwind.min.css')
        self.assertTrue(os.path.exists(css_path), 'Missing compiled Tailwind CSS')

    def test_dark_mode_compiled(self):
        import os
        from django.conf import settings
        css_path = os.path.join(settings.BASE_DIR, 'static', 'css', 'tailwind.min.css')
        with open(css_path) as f:
            self.assertIn('.dark\\:', f.read())

    # ── 反闪烁脚本 ──────────────────────────────────────────────

    def test_anti_fouc_script_present(self):
        html = self._get_html()
        self.assertIn('Anti-FOUC', html)
        self.assertIn('localStorage.getItem(\'theme\')', html)
        self.assertIn('prefers-color-scheme', html)

    def test_anti_fouc_script_before_meta(self):
        html = self._get_html()
        self.assertLess(html.index('Anti-FOUC'), html.index('<meta charset='))

    # ── 主题切换 JS ──────────────────────────────────────────────

    def test_theme_toggle_js_present(self):
        html = self._get_html()
        for func in ('getTheme', 'setTheme(', 'applyTheme(', 'cycleTheme'):
            self.assertIn(f'function {func}', html)
        self.assertIn('localStorage.setItem(\'theme\'', html)

    def test_theme_toggle_button_ids(self):
        html = self._get_html()
        for id_ in ('theme-toggle', 'theme-toggle-mobile', 'theme-icon-sun',
                     'theme-icon-moon', 'theme-icon-monitor', 'theme-label-mobile'):
            self.assertIn(f'id="{id_}"', html)

    def test_theme_toggle_defaults_to_system(self):
        self.assertIn('>主题：系统<', self._get_html())

    def test_cycle_theme_order(self):
        self.assertIn("current === 'light' ? 'dark' : current === 'dark' ? 'system' : 'light'", self._get_html())

    # ── body 双主题 class ────────────────────────────────────────

    def test_body_has_dual_theme_classes(self):
        html = self._get_html()
        body_tag = html.split('<body')[1].split('>')[0] if '<body' in html else ''
        for cls in ('bg-slate-50', 'dark:bg-[#050a14]', 'text-slate-700', 'dark:text-slate-300'):
            self.assertIn(cls, body_tag)

    # ── 模板 class 转换检查 ──────────────────────────────────────

    def test_nav_has_dual_theme_classes(self):
        html = self._get_html()
        self.assertIn('dark:bg-[#050a14]/60', html)
        self.assertIn('dark:border-slate-700', html)

    def test_footer_has_dual_theme_classes(self):
        self.assertIn('dark:border-slate-700 py-8 font-mono', self._get_html())

    def test_no_stale_dark_only_classes(self):
        html = self._get_html()
        for bad in ('class="bg-slate-950', 'class="bg-slate-900', 'class="bg-[#050a14]',
                     'class="text-slate-100"', 'class="text-slate-300"', 'class="text-slate-400"'):
            self.assertNotIn(bad, html)

    def test_login_page_dual_theme_inputs(self):
        html = self.client.get(reverse("login")).content.decode()
        for cls in ('dark:bg-slate-900', 'dark:border-slate-700', 'dark:text-slate-100'):
            self.assertIn(cls, html)

    def test_post_detail_page_dual_theme(self):
        post = Post.objects.create(
            title="Theme Test Post", author=self.user, status="published", body="Content.",
        )
        url = reverse("post_detail", kwargs={"username": self.user.username, "slug": post.slug})
        html = self.client.get(url).content.decode()
        self.assertIn('dark:bg-slate-950/60', html)
        self.assertIn('dark:text-slate-100', html)

    # ── forms.py widget class ────────────────────────────────────

    def test_post_form_widgets_dual_theme(self):
        html = self.client.get(reverse("post_create")).content.decode()
        for cls in ('dark:bg-slate-900', 'dark:border-slate-700', 'dark:text-slate-100', 'dark:focus:border-cyan-500'):
            self.assertIn(cls, html)

    def test_memo_form_widgets_dual_theme(self):
        self.assertIn('dark:bg-slate-900', self.client.get(reverse("memo_create")).content.decode())

    def test_series_form_widgets_dual_theme(self):
        self.assertIn('dark:bg-slate-900', self.client.get(reverse("series_create")).content.decode())

    def test_profile_edit_widgets_dual_theme(self):
        self.assertIn('dark:bg-slate-900', self.client.get(reverse("profile_edit")).content.decode())

    # ── copy-btn 与 back-to-top 双主题 CSS ────────────────────────

    def test_copy_btn_has_dark_css_override(self):
        self.assertIn('html.dark .copy-btn', self._get_html())

    def test_back_to_top_has_dark_css_override(self):
        self.assertIn('html.dark #back-to-top', self._get_html())

    def test_codehilite_has_both_theme_css(self):
        html = self._get_html()
        self.assertIn('html.dark .codehilite', html)
        self.assertIn('html:not(.dark) .codehilite', html)

    def test_md_content_has_both_theme_css(self):
        html = self._get_html()
        self.assertIn('html.dark .md-content h1', html)
        self.assertIn('html:not(.dark) .md-content h1', html)

    # ── 表单提交页渲染（确保 url 反向解析不出错） ──────────────────

    def test_post_create_page_renders(self):
        self.assertEqual(self.client.get(reverse("post_create")).status_code, 200)

    def test_memo_create_page_renders(self):
        self.assertEqual(self.client.get(reverse("memo_create")).status_code, 200)

    def test_series_create_page_renders(self):
        self.assertEqual(self.client.get(reverse("series_create")).status_code, 200)

    def test_profile_edit_page_renders(self):
        self.assertEqual(self.client.get(reverse("profile_edit")).status_code, 200)


class ImageUploadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="img_uploader", password="pw")
        self.client.login(username="img_uploader", password="pw")
        self.url = reverse("image_upload_ajax")
        from io import BytesIO
        from PIL import Image as PILImage
        img = PILImage.new("RGB", (100, 80), color=(200, 100, 50))
        self.png_buf = BytesIO()
        img.save(self.png_buf, format="PNG")
        self.png_buf.seek(0)
        self.png_data = self.png_buf.read()

    def _upload(self, data=None, filename="test.png"):
        buf = data or self.png_data
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile(filename, buf, content_type="image/png")
        return self.client.post(self.url, {"image": f})

    def test_upload_unauthenticated(self):
        self.client.logout()
        resp = self._upload()
        self.assertEqual(resp.status_code, 403)
        self.assertIn("error", resp.json())

    def test_upload_valid_image(self):
        resp = self._upload()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("url", data)
        self.assertIn("/media/uploads/images/", data["url"])
        self.assertTrue(data["url"].endswith(".webp"))
        self.assertEqual(data["name"], "test.png")
        self.assertGreater(data["size"], 0)
        self.assertGreater(data["width"], 0)

    def test_upload_no_file(self):
        resp = self.client.post(self.url, {})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_upload_invalid_file(self):
        resp = self._upload(data=b"this is not an image file", filename="fake.png")
        self.assertEqual(resp.status_code, 400)

    def test_upload_deduplication(self):
        resp1 = self._upload()
        url1 = resp1.json()["url"]
        resp2 = self._upload()
        self.assertEqual(url1, resp2.json()["url"])
        self.assertTrue(resp2.json().get("dedup"))
        self.assertEqual(UploadedImage.objects.count(), 1)

    def test_upload_oversized(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        huge = SimpleUploadedFile("big.jpg", b"x" * (10 * 1024 * 1024 + 1), content_type="image/jpeg")
        resp = self.client.post(self.url, {"image": huge})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("10MB", resp.json()["error"])

    def test_webp_output(self):
        resp = self._upload()
        self.assertTrue(resp.json()["url"].endswith(".webp"))
        obj = UploadedImage.objects.first()
        self.assertTrue(obj.image.name.endswith(".webp"))
        self.assertGreater(obj.file_size, 0)

    def test_resize_large_image(self):
        from io import BytesIO
        from PIL import Image as PILImage
        from django.core.files.uploadedfile import SimpleUploadedFile
        big = PILImage.new("RGB", (3000, 1500), color=(0, 100, 200))
        buf = BytesIO()
        big.save(buf, format="PNG")
        buf.seek(0)
        f = SimpleUploadedFile("big.png", buf.read(), content_type="image/png")
        resp = self.client.post(self.url, {"image": f})
        data = resp.json()
        self.assertEqual(data["width"], 2000)
        self.assertEqual(data["height"], 1000)

    def test_strips_exif_metadata(self):
        from io import BytesIO
        from PIL import Image as PILImage
        from django.core.files.uploadedfile import SimpleUploadedFile
        img = PILImage.new("RGB", (50, 50), color=(255, 0, 0))
        exif = img.getexif()
        exif[0x010F] = "TestCamera"
        buf = BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        buf.seek(0)
        self.client.post(self.url, {"image": SimpleUploadedFile("exif.jpg", buf.read(), content_type="image/jpeg")})
        stored = PILImage.open(UploadedImage.objects.first().image.path)
        self.assertEqual(len(stored.getexif()), 0)

    def test_image_upload_page_has_upload_button(self):
        resp = self.client.get(reverse("post_create"))
        self.assertContains(resp, 'id="image-upload-btn"')
        self.assertContains(resp, 'id="image-file-input"')
        self.assertContains(resp, '/ajax/image/upload/')


class NotificationTests(TestCase):
    """Test notification creation, read/unread, and context processor."""

    def setUp(self):
        self.client = Client()
        self.author = User.objects.create_user(username="post_author", password="pw")
        self.reader = User.objects.create_user(username="reader", password="pw")
        self.post = Post.objects.create(
            title="测试文章", author=self.author, body="文章正文内容", status="published",
        )
        self.ct = ContentType.objects.get_for_model(Post)

    def _like_post(self, user):
        self.client.login(username=user.username, password="pw")
        import json
        return self.client.post(
            reverse("like_toggle_ajax"),
            data=json.dumps({"content_type": "blog.post", "object_id": self.post.pk}),
            content_type="application/json",
        )

    def _comment_post(self, user):
        self.client.login(username=user.username, password="pw")
        return self.client.post(
            reverse("comment_create"),
            data={
                "content_type": "blog.post", "object_id": self.post.pk,
                "content": "写得真好！",
                "next": reverse("post_detail", kwargs={"username": self.author.username, "slug": self.post.slug}),
            },
        )

    # ── Like notifications ──────────────────────────────────────────

    def test_like_creates_notification_for_author(self):
        self._like_post(self.reader)
        self.assertEqual(Notification.objects.count(), 1)
        notif = Notification.objects.first()
        self.assertEqual(notif.recipient, self.author)
        self.assertEqual(notif.actor, self.reader)
        self.assertEqual(notif.notification_type, "like")
        self.assertFalse(notif.is_read)
        self.assertIn("测试文章", notif.message)

    def test_self_like_does_not_create_notification(self):
        self._like_post(self.author)
        self.assertEqual(Notification.objects.count(), 0)

    def test_unlike_does_not_create_notification(self):
        self._like_post(self.reader)
        self._like_post(self.reader)
        self.assertEqual(Notification.objects.count(), 1)

    # ── Comment notifications ───────────────────────────────────────

    def test_comment_creates_notification_for_author(self):
        self._comment_post(self.reader)
        self.assertEqual(Notification.objects.count(), 1)
        notif = Notification.objects.first()
        self.assertEqual(notif.recipient, self.author)
        self.assertEqual(notif.notification_type, "comment")
        self.assertFalse(notif.is_read)
        self.assertIn("测试文章", notif.message)

    def test_self_comment_does_not_create_notification(self):
        self._comment_post(self.author)
        self.assertEqual(Notification.objects.count(), 0)

    # ── Read / mark all read ────────────────────────────────────────

    def test_notification_read_marks_as_read_and_redirects(self):
        self._like_post(self.reader)
        notif = Notification.objects.first()
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("notification_read", kwargs={"pk": notif.pk}))
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertRedirects(resp, reverse("post_detail", kwargs={"username": self.author.username, "slug": self.post.slug}))

    def test_notification_read_rejects_wrong_recipient(self):
        self._like_post(self.reader)
        notif = Notification.objects.first()
        self.client.login(username="reader", password="pw")
        self.assertEqual(self.client.get(reverse("notification_read", kwargs={"pk": notif.pk})).status_code, 404)

    def test_mark_all_read(self):
        post2 = Post.objects.create(title="第二篇", author=self.author, body="内容", status="published")
        ct = ContentType.objects.get_for_model(Post)
        self._like_post(self.reader)
        Notification.objects.create(
            recipient=self.author, actor=self.reader, notification_type="like",
            message="reader 赞了你的文章《第二篇》", content_type=ct, object_id=post2.pk,
        )
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 2)
        self.client.login(username="post_author", password="pw")
        self.client.post(reverse("notification_mark_all_read"))
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 0)

    # ── Context processor ────────────────────────────────────────────

    def test_context_processor_injects_unread_count(self):
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        self.assertEqual(self.client.get(reverse("index")).context["unread_notifications_count"], 1)

    def test_context_processor_zero_for_anonymous(self):
        self.assertEqual(self.client.get(reverse("index")).context["unread_notifications_count"], 0)

    def test_context_processor_zero_when_all_read(self):
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        self.client.post(reverse("notification_mark_all_read"))
        self.assertEqual(self.client.get(reverse("index")).context["unread_notifications_count"], 0)

    # ── Nav rendering ────────────────────────────────────────────────

    def test_nav_red_dot_present_when_unread(self):
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, 'inline-flex items-center gap-1')
        self.assertContains(resp, 'rounded-full bg-red-500')

    def test_nav_red_dot_absent_when_no_unread(self):
        self.client.login(username="post_author", password="pw")
        self.assertNotContains(self.client.get(reverse("index")), 'rounded-full bg-red-500')

    # ── Inbox page ───────────────────────────────────────────────────

    def test_inbox_requires_login(self):
        self.assertEqual(self.client.get(reverse("inbox")).status_code, 302)

    def test_inbox_renders(self):
        self.client.login(username="post_author", password="pw")
        self.assertEqual(self.client.get(reverse("inbox")).status_code, 200)

    def test_inbox_shows_notifications(self):
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        self.assertContains(self.client.get(reverse("inbox")), "测试文章")

    def test_inbox_empty_state(self):
        self.client.login(username="post_author", password="pw")
        self.assertContains(self.client.get(reverse("inbox")), "信箱空空如也")

    def test_inbox_mark_all_read_button(self):
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        self.assertContains(self.client.get(reverse("inbox")), "全部标为已读")

    # ── get_target_url ───────────────────────────────────────────────

    def test_get_target_url_for_post(self):
        notif = Notification.objects.create(
            recipient=self.author, actor=self.reader, notification_type="like",
            message="reader 赞了你的文章《测试文章》",
            content_type=self.ct, object_id=self.post.pk,
        )
        expected = reverse("post_detail", kwargs={"username": self.author.username, "slug": self.post.slug})
        self.assertEqual(notif.get_target_url(), expected)

    def test_get_target_url_for_memo(self):
        memo = Memo.objects.create(content="Test memo", is_public=True, author=self.author)
        ct = ContentType.objects.get_for_model(Memo)
        notif = Notification.objects.create(
            recipient=self.author, actor=self.reader, notification_type="like",
            message="...", content_type=ct, object_id=memo.pk,
        )
        expected = reverse("memo_detail", kwargs={"username": self.author.username, "pk": memo.pk})
        self.assertEqual(notif.get_target_url(), expected)
