"""
Tests for MeLi Cosmos blog app (multi-user).
"""
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from .models import Category, Tag, Post, Memo, Series, UserProfile, InviteCode, UploadedImage, Notification


class CategoryModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cat_test_user", password="pw")

    def test_slug_auto_generated(self):
        cat = Category.objects.create(name="测试分类", author=self.user)
        self.assertEqual(cat.slug, "测试分类")

    def test_str_returns_name(self):
        cat = Category.objects.create(name="测试分类", author=self.user)
        self.assertEqual(str(cat), "测试分类")

    def test_unique_per_author(self):
        """Same slug can be used by different authors."""
        user2 = User.objects.create_user(username="other_cat", password="pw")
        Category.objects.create(name="Dev", author=self.user)
        Category.objects.create(name="Dev", author=user2)
        self.assertEqual(Category.objects.filter(slug="dev").count(), 2)


class TagModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tag_test_user", password="pw")

    def test_slug_auto_generated(self):
        tag = Tag.objects.create(name="测试标签", author=self.user)
        self.assertEqual(tag.slug, "测试标签")

    def test_str_returns_name(self):
        tag = Tag.objects.create(name="测试标签", author=self.user)
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

    def test_default_status_is_published(self):
        post = Post.objects.create(title="D", author=self.user)
        self.assertEqual(post.status, "published")


class UserProfileTests(TestCase):
    def test_profile_auto_created_on_user_creation(self):
        user = User.objects.create_user(username="new_user", password="pw")
        self.assertTrue(hasattr(user, "profile"))
        self.assertIsInstance(user.profile, UserProfile)
        self.assertEqual(user.profile.display_name, "new_user")


class InviteCodeTests(TestCase):
    def setUp(self):
        self.inviter = User.objects.create_user(username="inviter", password="pw")
        self.client = Client()

    def test_generate_code(self):
        code = InviteCode.generate_code()
        self.assertEqual(len(code), 32)

    def test_code_expiration(self):
        code = InviteCode.objects.create(
            code=InviteCode.generate_code(),
            inviter=self.inviter,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        self.assertFalse(code.is_expired)

        expired = InviteCode.objects.create(
            code=InviteCode.generate_code(),
            inviter=self.inviter,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(expired.is_expired)

    def test_daily_limit(self):
        """Non-staff users can only generate 1 unused code per day."""
        self.client.login(username="inviter", password="pw")
        # Create one code today
        InviteCode.objects.create(
            code=InviteCode.generate_code(),
            inviter=self.inviter,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        resp = self.client.get(reverse("invite"))
        self.assertFalse(resp.context["can_generate"])

    def test_staff_no_limit(self):
        """Staff users have no daily limit."""
        staff = User.objects.create_user(username="staff", password="pw", is_staff=True)
        self.client.login(username="staff", password="pw")
        InviteCode.objects.create(
            code=InviteCode.generate_code(),
            inviter=staff,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        resp = self.client.get(reverse("invite"))
        self.assertTrue(resp.context["can_generate"])

    def test_generate_invite_post(self):
        self.client.login(username="inviter", password="pw")
        resp = self.client.post(reverse("invite"))
        self.assertRedirects(resp, reverse("invite"))
        self.assertEqual(InviteCode.objects.filter(inviter=self.inviter).count(), 1)


class ViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="bar", password="foo")
        self.cat = Category.objects.create(name="Dev", author=self.user)
        self.tag = Tag.objects.create(name="Python", author=self.user)
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
        resp = self.client.get(reverse("post_detail", kwargs={"username": self.user.username, "slug": self.post.slug}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello World")

    def test_post_detail_by_pk_200(self):
        resp = self.client.get(reverse("post_detail", kwargs={"username": self.user.username, "slug": str(self.post.pk)}))
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
        resp = self.client.get(reverse("post_detail", kwargs={"username": self.user.username, "slug": draft.slug}))
        self.assertEqual(resp.status_code, 404)

        # Index should not show draft
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, "Secret Draft")

    def test_user_space_200(self):
        resp = self.client.get(reverse("user_space", kwargs={"username": self.user.username}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello World")

    def test_legacy_post_detail_redirects(self):
        """Old /post/<slug>/ URL still works as a legacy route."""
        resp = self.client.get(reverse("post_detail_legacy", kwargs={"slug": self.post.slug}))
        self.assertEqual(resp.status_code, 200)


class MultiUserVisibilityTests(TestCase):
    """Test that content is properly isolated between users."""

    def setUp(self):
        self.client = Client()
        self.user_a = User.objects.create_user(username="alice", password="pw")
        self.user_b = User.objects.create_user(username="bob", password="pw")

        # User A's published post
        self.post_a = Post.objects.create(
            title="Alice Post",
            author=self.user_a,
            status="published",
        )
        # User A's draft
        self.draft_a = Post.objects.create(
            title="Alice Draft",
            author=self.user_a,
            status="draft",
        )
        # User B's published post
        self.post_b = Post.objects.create(
            title="Bob Post",
            author=self.user_b,
            status="published",
        )

    def test_both_published_posts_visible_on_index(self):
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, "Alice Post")
        self.assertContains(resp, "Bob Post")

    def test_user_b_cannot_see_user_a_draft(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse(
            "post_detail",
            kwargs={"username": "alice", "slug": self.draft_a.slug},
        ))
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


class MultiUserCategoryTagTests(TestCase):
    """Test that categories and tags are isolated per user."""

    def setUp(self):
        self.user_a = User.objects.create_user(username="alice", password="pw")
        self.user_b = User.objects.create_user(username="bob", password="pw")
        self.cat_a = Category.objects.create(name="Python", author=self.user_a)
        self.cat_b = Category.objects.create(name="Python", author=self.user_b)
        self.post_a = Post.objects.create(
            title="Alice Python Post",
            author=self.user_a,
            category=self.cat_a,
            status="published",
        )

    def test_same_slug_different_authors(self):
        self.assertEqual(Category.objects.filter(slug="python").count(), 2)

    def test_user_category_shows_own_posts(self):
        resp = self.client.get(reverse(
            "user_post_by_category",
            kwargs={"username": "alice", "slug": self.cat_a.slug},
        ))
        self.assertContains(resp, "Alice Python Post")


class MemoAuthorTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user_a = User.objects.create_user(username="alice", password="pw")
        self.user_b = User.objects.create_user(username="bob", password="pw")
        self.memo_a = Memo.objects.create(content="Alice memo", is_public=True, author=self.user_a)
        Memo.objects.create(content="Alice private memo", is_public=False, author=self.user_a)
        Memo.objects.create(content="Bob memo", is_public=True, author=self.user_b)

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
        resp = self.client.get(reverse("post_detail", kwargs={"username": self.author.username, "slug": draft.slug}))
        self.assertEqual(resp.status_code, 200)


class MemoTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="memo_user", password="pw")
        self.memo = Memo.objects.create(content="Public memo", is_public=True, author=self.user)
        Memo.objects.create(content="Private memo", is_public=False, author=self.user)

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
        """<html> 初始携带 class='dark'。"""
        html = self._get_html()
        self.assertIn('<html lang="zh-CN" class="dark">', html)

    def test_tailwind_css_is_compiled(self):
        """Tailwind v4 CSS 预编译为静态文件，不再使用浏览器 CDN。"""
        import os
        from django.conf import settings
        css_path = os.path.join(settings.BASE_DIR, 'static', 'css', 'tailwind.min.css')
        self.assertTrue(os.path.exists(css_path), 'Missing compiled Tailwind CSS')

    def test_dark_mode_compiled(self):
        """编译后的 CSS 应包含 dark 变体规则（CLI 已处理 @custom-variant）。"""
        import os
        from django.conf import settings
        css_path = os.path.join(settings.BASE_DIR, 'static', 'css', 'tailwind.min.css')
        with open(css_path) as f:
            content = f.read()
        self.assertIn('.dark\\:', content)

    # ── 反闪烁脚本 ──────────────────────────────────────────────

    def test_anti_fouc_script_present(self):
        """<head> 中包含反闪烁脚本。"""
        html = self._get_html()
        self.assertIn('Anti-FOUC', html)
        self.assertIn('localStorage.getItem(\'theme\')', html)
        self.assertIn('prefers-color-scheme', html)

    def test_anti_fouc_script_before_meta(self):
        """反闪烁脚本在 <meta> 之前执行，避免白闪。"""
        html = self._get_html()
        script_pos = html.index('Anti-FOUC')
        meta_pos = html.index('<meta charset=')
        self.assertLess(script_pos, meta_pos)

    # ── 主题切换 JS ──────────────────────────────────────────────

    def test_theme_toggle_js_present(self):
        """页面尾部包含主题切换逻辑。"""
        html = self._get_html()
        self.assertIn('function getTheme()', html)
        self.assertIn('function setTheme(', html)
        self.assertIn('function applyTheme(', html)
        self.assertIn('function cycleTheme()', html)
        self.assertIn('localStorage.setItem(\'theme\'', html)
        self.assertIn('prefers-color-scheme: light', html)

    def test_theme_toggle_button_ids(self):
        """桌面/移动切换按钮具备正确 id。"""
        html = self._get_html()
        self.assertIn('id="theme-toggle"', html)
        self.assertIn('id="theme-toggle-mobile"', html)
        self.assertIn('id="theme-icon-sun"', html)
        self.assertIn('id="theme-icon-moon"', html)
        self.assertIn('id="theme-icon-monitor"', html)
        self.assertIn('id="theme-label-mobile"', html)

    def test_theme_toggle_defaults_to_system(self):
        """初始加载时默认显示"系统"主题。"""
        html = self._get_html()
        self.assertIn('>主题：系统<', html)

    def test_cycle_theme_order(self):
        """切换顺序: light → dark → system → light。"""
        html = self._get_html()
        self.assertIn("current === 'light' ? 'dark' : current === 'dark' ? 'system' : 'light'", html)

    # ── body 双主题 class ────────────────────────────────────────

    def test_body_has_dual_theme_classes(self):
        """<body> 同时包含 light 基础色和 dark: 变体。"""
        html = self._get_html()
        body_tag = html.split('<body')[1].split('>')[0] if '<body' in html else ''
        self.assertIn('bg-slate-50', body_tag)
        self.assertIn('dark:bg-[#050a14]', body_tag)
        self.assertIn('text-slate-700', body_tag)
        self.assertIn('dark:text-slate-300', body_tag)

    # ── 模板 class 转换检查 ──────────────────────────────────────

    def test_nav_has_dual_theme_classes(self):
        """Nav 背景和边框使用双主题 class。"""
        html = self._get_html()
        self.assertIn('dark:bg-[#050a14]/60', html)
        self.assertIn('dark:border-slate-700', html)

    def test_footer_has_dual_theme_classes(self):
        """Footer 使用双主题 class。"""
        html = self._get_html()
        self.assertIn('dark:border-slate-700 py-8 font-mono', html)

    def test_index_values_banner_dual_theme(self):
        """首页价值声明使用双主题 class。"""
        html = self._get_html()
        self.assertIn('dark:text-slate-400 text-center', html)

    def test_no_stale_dark_only_bg_in_templates(self):
        """模板中不应出现旧的纯深色背景（无 light 兜底）。"""
        html = self._get_html()
        # 这类 class 不应作为基础值出现
        self.assertNotIn('class="bg-slate-950', html)
        self.assertNotIn('class="bg-slate-900', html)
        self.assertNotIn('class="bg-[#050a14]', html)

    def test_no_stale_dark_only_text_in_templates(self):
        """模板中不应出现旧的纯深色文字（无 light 兜底）。"""
        html = self._get_html()
        self.assertNotIn('class="text-slate-100"', html)
        self.assertNotIn('class="text-slate-300"', html)
        self.assertNotIn('class="text-slate-400"', html)

    def test_login_page_dual_theme_inputs(self):
        """登录页表单项使用双主题 class。"""
        resp = self.client.get(reverse("login"))
        html = resp.content.decode()
        self.assertIn('dark:bg-slate-900', html)
        self.assertIn('dark:border-slate-700', html)
        self.assertIn('dark:text-slate-100', html)

    def test_post_detail_page_dual_theme(self):
        """文章详情页使用双主题 class。"""
        post = Post.objects.create(
            title="Theme Test Post",
            author=self.user,
            status="published",
            body="Content for theme test.",
        )
        url = reverse("post_detail", kwargs={"username": self.user.username, "slug": post.slug})
        resp = self.client.get(url)
        html = resp.content.decode()
        self.assertIn('dark:bg-slate-950/60', html)
        self.assertIn('dark:text-slate-100', html)

    # ── forms.py widget class ────────────────────────────────────

    def test_post_form_widgets_dual_theme(self):
        """PostForm 的 widget attrs 使用双主题 class。"""
        resp = self.client.get(reverse("post_create"))
        html = resp.content.decode()
        self.assertIn('dark:bg-slate-900', html)
        self.assertIn('dark:border-slate-700', html)
        self.assertIn('dark:text-slate-100', html)
        self.assertIn('dark:focus:border-cyan-500', html)

    def test_memo_form_widgets_dual_theme(self):
        """MemoForm 的 widget attrs 使用双主题 class。"""
        resp = self.client.get(reverse("memo_create"))
        html = resp.content.decode()
        self.assertIn('dark:bg-slate-900', html)

    def test_series_form_widgets_dual_theme(self):
        """SeriesForm 的 widget attrs 使用双主题 class。"""
        resp = self.client.get(reverse("series_create"))
        html = resp.content.decode()
        self.assertIn('dark:bg-slate-900', html)

    def test_profile_edit_widgets_dual_theme(self):
        """UserProfileForm 的 widget attrs 使用双主题 class。"""
        resp = self.client.get(reverse("profile_edit"))
        html = resp.content.decode()
        self.assertIn('dark:bg-slate-900', html)

    # ── copy-btn 与 back-to-top 双主题 CSS ────────────────────────

    def test_copy_btn_has_dark_css_override(self):
        """copy-btn 在 style 中有 html.dark 覆盖规则。"""
        html = self._get_html()
        self.assertIn('html.dark .copy-btn', html)

    def test_back_to_top_has_dark_css_override(self):
        """back-to-top 在 style 中有 html.dark 覆盖规则。"""
        html = self._get_html()
        self.assertIn('html.dark #back-to-top', html)

    def test_codehilite_has_both_theme_css(self):
        """代码高亮同时具备 html.dark 和 html:not(.dark) 规则。"""
        html = self._get_html()
        self.assertIn('html.dark .codehilite', html)
        self.assertIn('html:not(.dark) .codehilite', html)

    def test_md_content_has_both_theme_css(self):
        """Markdown 排版同时具备 html.dark 和 html:not(.dark) 规则。"""
        html = self._get_html()
        self.assertIn('html.dark .md-content h1', html)
        self.assertIn('html:not(.dark) .md-content h1', html)

    # ── 表单提交页渲染（确保 url 反向解析不出错） ──────────────────

    def test_post_create_page_renders(self):
        resp = self.client.get(reverse("post_create"))
        self.assertEqual(resp.status_code, 200)

    def test_memo_create_page_renders(self):
        resp = self.client.get(reverse("memo_create"))
        self.assertEqual(resp.status_code, 200)

    def test_series_create_page_renders(self):
        resp = self.client.get(reverse("series_create"))
        self.assertEqual(resp.status_code, 200)

    def test_profile_edit_page_renders(self):
        resp = self.client.get(reverse("profile_edit"))
        self.assertEqual(resp.status_code, 200)


class ImageUploadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="img_uploader", password="pw")
        self.client.login(username="img_uploader", password="pw")
        self.url = reverse("image_upload_ajax")

        # Create a small test PNG image
        from io import BytesIO
        from PIL import Image as PILImage
        img = PILImage.new("RGB", (100, 80), color=(200, 100, 50))
        self.png_buf = BytesIO()
        img.save(self.png_buf, format="PNG")
        self.png_buf.seek(0)
        self.png_data = self.png_buf.read()

    def _upload(self, data=None, filename="test.png"):
        buf = data or self.png_data
        from io import BytesIO
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
        self.assertGreater(data["height"], 0)
        self.assertFalse(data.get("dedup"))

    def test_upload_no_file(self):
        resp = self.client.post(self.url, {})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_upload_invalid_file(self):
        resp = self._upload(data=b"this is not an image file", filename="fake.png")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_upload_deduplication(self):
        """Uploading the same image twice returns the same URL."""
        resp1 = self._upload()
        self.assertEqual(resp1.status_code, 200)
        url1 = resp1.json()["url"]

        resp2 = self._upload()
        self.assertEqual(resp2.status_code, 200)
        url2 = resp2.json()["url"]

        self.assertEqual(url1, url2)
        self.assertTrue(resp2.json().get("dedup"))

        # Only one DB row
        from .models import UploadedImage
        self.assertEqual(UploadedImage.objects.count(), 1)

    def test_upload_oversized(self):
        """Files over 10MB should be rejected."""
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        huge = SimpleUploadedFile("big.jpg", b"x" * (10 * 1024 * 1024 + 1), content_type="image/jpeg")
        resp = self.client.post(self.url, {"image": huge})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("10MB", resp.json()["error"])

    def test_webp_output(self):
        """Uploaded image is stored as WebP."""
        resp = self._upload()
        url = resp.json()["url"]
        self.assertTrue(url.endswith(".webp"))

        # Verify the stored file exists and is valid WebP
        from .models import UploadedImage
        obj = UploadedImage.objects.first()
        self.assertIsNotNone(obj)
        self.assertTrue(obj.image.name.endswith(".webp"))
        self.assertGreater(obj.file_size, 0)

    def test_resize_large_image(self):
        """Image larger than 2000px should be resized."""
        from io import BytesIO
        from PIL import Image as PILImage
        big = PILImage.new("RGB", (3000, 1500), color=(0, 100, 200))
        buf = BytesIO()
        big.save(buf, format="PNG")
        buf.seek(0)

        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("big.png", buf.read(), content_type="image/png")
        resp = self.client.post(self.url, {"image": f})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["width"], 2000)
        self.assertEqual(data["height"], 1000)

    def test_strips_exif_metadata(self):
        """EXIF metadata should be stripped from processed images."""
        from io import BytesIO
        from PIL import Image as PILImage

        # Create an image with EXIF data
        img = PILImage.new("RGB", (50, 50), color=(255, 0, 0))
        exif = img.getexif()
        exif[0x010F] = "TestCamera"  # Make
        exif[0x0110] = "TestModel"   # Model

        buf = BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        buf.seek(0)

        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("exif.jpg", buf.read(), content_type="image/jpeg")
        resp = self.client.post(self.url, {"image": f})
        self.assertEqual(resp.status_code, 200)

        # Re-open the stored WebP and verify no EXIF
        from .models import UploadedImage
        obj = UploadedImage.objects.first()
        stored = PILImage.open(obj.image.path)
        stored_exif = stored.getexif()
        self.assertEqual(len(stored_exif), 0)

    def test_image_upload_page_has_upload_button(self):
        """Post create page should have the image upload button."""
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
            title="测试文章",
            author=self.author,
            body="文章正文内容",
            status="published",
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
                "content_type": "blog.post",
                "object_id": self.post.pk,
                "content": "写得真好！",
                "next": reverse("post_detail", kwargs={"username": self.author.username, "slug": self.post.slug}),
            },
        )

    # ── Like notifications ──────────────────────────────────────────

    def test_like_creates_notification_for_author(self):
        """Reader liking author's post creates a notification."""
        self._like_post(self.reader)
        self.assertEqual(Notification.objects.count(), 1)
        notif = Notification.objects.first()
        self.assertEqual(notif.recipient, self.author)
        self.assertEqual(notif.actor, self.reader)
        self.assertEqual(notif.notification_type, "like")
        self.assertFalse(notif.is_read)
        self.assertIn("测试文章", notif.message)
        self.assertIn("reader", notif.message)

    def test_self_like_does_not_create_notification(self):
        """Author liking their own post should not create a notification."""
        self._like_post(self.author)
        self.assertEqual(Notification.objects.count(), 0)

    def test_unlike_does_not_create_notification(self):
        """Toggling like off should not create a notification."""
        self._like_post(self.reader)  # like
        self.assertEqual(Notification.objects.count(), 1)
        self._like_post(self.reader)  # unlike
        self.assertEqual(Notification.objects.count(), 1)

    # ── Comment notifications ───────────────────────────────────────

    def test_comment_creates_notification_for_author(self):
        """Reader commenting on author's post creates a notification."""
        self._comment_post(self.reader)
        self.assertEqual(Notification.objects.count(), 1)
        notif = Notification.objects.first()
        self.assertEqual(notif.recipient, self.author)
        self.assertEqual(notif.actor, self.reader)
        self.assertEqual(notif.notification_type, "comment")
        self.assertFalse(notif.is_read)
        self.assertIn("测试文章", notif.message)
        self.assertIn("reader", notif.message)

    def test_self_comment_does_not_create_notification(self):
        """Author commenting on their own post should not create a notification."""
        self._comment_post(self.author)
        self.assertEqual(Notification.objects.count(), 0)

    # ── Read / mark all read ────────────────────────────────────────

    def test_notification_read_marks_as_read_and_redirects(self):
        """Clicking a notification marks it read and redirects to the post."""
        self._like_post(self.reader)
        notif = Notification.objects.first()
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("notification_read", kwargs={"pk": notif.pk}))
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertRedirects(resp, reverse(
            "post_detail", kwargs={"username": self.author.username, "slug": self.post.slug},
        ))

    def test_notification_read_rejects_wrong_recipient(self):
        """Only the recipient can mark a notification as read."""
        self._like_post(self.reader)
        notif = Notification.objects.first()
        self.client.login(username="reader", password="pw")
        resp = self.client.get(reverse("notification_read", kwargs={"pk": notif.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_mark_all_read(self):
        """Mark all read should update all unread notifications."""
        post2 = Post.objects.create(title="第二篇", author=self.author, body="内容", status="published")
        ct = ContentType.objects.get_for_model(Post)
        self._like_post(self.reader)
        # Create a second notification manually
        Notification.objects.create(
            recipient=self.author, actor=self.reader, notification_type="like",
            message="reader 赞了你的文章《第二篇》", content_type=ct, object_id=post2.pk,
        )
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 2)

        self.client.login(username="post_author", password="pw")
        resp = self.client.post(reverse("notification_mark_all_read"))
        self.assertRedirects(resp, reverse("inbox"))
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 0)

    # ── Context processor ────────────────────────────────────────────

    def test_context_processor_injects_unread_count_for_authenticated(self):
        """Authenticated user gets unread_notifications_count in context."""
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.context["unread_notifications_count"], 1)

    def test_context_processor_injects_zero_for_anonymous(self):
        """Anonymous user gets unread_notifications_count = 0."""
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.context["unread_notifications_count"], 0)

    def test_context_processor_zero_when_all_read(self):
        """After marking all read, unread count is 0."""
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        self.client.post(reverse("notification_mark_all_read"))
        resp = self.client.get(reverse("index"))
        self.assertEqual(resp.context["unread_notifications_count"], 0)

    # ── Nav rendering ────────────────────────────────────────────────

    def test_nav_red_dot_present_when_unread(self):
        """Nav renders a red dot when there are unread notifications."""
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("index"))
        self.assertContains(resp, 'inline-flex items-center gap-1')
        self.assertContains(resp, 'rounded-full bg-red-500')

    def test_nav_red_dot_absent_when_no_unread(self):
        """Nav does NOT render the red dot when there are no unread notifications."""
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("index"))
        self.assertNotContains(resp, 'rounded-full bg-red-500')

    # ── Inbox page ───────────────────────────────────────────────────

    def test_inbox_requires_login(self):
        """Inbox should redirect anonymous users to login."""
        resp = self.client.get(reverse("inbox"))
        self.assertEqual(resp.status_code, 302)

    def test_inbox_renders_for_authenticated(self):
        """Authenticated user can access inbox."""
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("inbox"))
        self.assertEqual(resp.status_code, 200)

    def test_inbox_shows_notifications(self):
        """Inbox lists notifications for the current user."""
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("inbox"))
        self.assertContains(resp, "测试文章")

    def test_inbox_empty_state(self):
        """Empty inbox shows the empty state message."""
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("inbox"))
        self.assertContains(resp, "信箱空空如也")

    def test_inbox_shows_mark_all_read_button_when_unread(self):
        """Mark all read button appears when there are unread notifications."""
        self._like_post(self.reader)
        self.client.login(username="post_author", password="pw")
        resp = self.client.get(reverse("inbox"))
        self.assertContains(resp, "全部标为已读")

    # ── get_target_url ───────────────────────────────────────────────

    def test_get_target_url_for_post(self):
        """Notification for a post returns the post detail URL."""
        notif = Notification.objects.create(
            recipient=self.author, actor=self.reader, notification_type="like",
            message="reader 赞了你的文章《测试文章》",
            content_type=self.ct, object_id=self.post.pk,
        )
        expected = reverse("post_detail", kwargs={"username": self.author.username, "slug": self.post.slug})
        self.assertEqual(notif.get_target_url(), expected)

    def test_get_target_url_for_memo(self):
        """Notification for a memo returns the memo detail URL."""
        memo = Memo.objects.create(content="Test memo", is_public=True, author=self.author)
        ct = ContentType.objects.get_for_model(Memo)
        notif = Notification.objects.create(
            recipient=self.author, actor=self.reader, notification_type="like",
            message="...", content_type=ct, object_id=memo.pk,
        )
        expected = reverse("memo_detail", kwargs={"username": self.author.username, "pk": memo.pk})
        self.assertEqual(notif.get_target_url(), expected)
