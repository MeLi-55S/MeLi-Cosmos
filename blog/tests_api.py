"""REST API（``/api/v1``）测试。

覆盖：令牌签发与鉴权、错误响应格式、文章/碎碎念的读写与可见性规则、分类法、
发现类只读端点（用户 / 归档 / 搜索）、评论与点赞、收件箱、账号与令牌管理、上传。
运行：``uv run python manage.py test tests_api``（或整包 ``manage.py test``）。
"""

import json
import re
import shutil
import tempfile
from io import BytesIO
from urllib.parse import quote

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image as PILImage

from . import models as blog_models
from .api.app import api
from .api.routers.account import (
    LOGIN_LIMIT,
    LOGIN_WINDOW,
    MAX_ACTIVE_TOKENS,
    REGISTER_LIMIT,
    REGISTER_WINDOW,
)
from .models import ApiToken, Category, Comment, Memo, Notification, Post, Tag

# 与 blog/tests.py 相同的处理：测试期间不下载 DiceBear 头像
_original_generate = blog_models.generate_default_avatar


def _mock_generate(profile):
    from django.core.files.base import ContentFile
    profile.avatar.save(f"{profile.user.username}.webp", ContentFile(b""), save=True)
    return True


blog_models.generate_default_avatar = _mock_generate


class ApiTestCase(TestCase):
    """公共夹具：两个作者、各自的令牌与若干内容。"""

    def setUp(self):
        # 限流计数走 LocMemCache，进程内跨用例累积；不清就会互相干扰
        from django.core.cache import cache
        cache.clear()

        self.alice = User.objects.create_user(username="alice", password="pw-alice")
        self.bob = User.objects.create_user(username="bob", password="pw-bob")
        self.alice_token, self.alice_raw = ApiToken.issue(self.alice, "test")
        self.bob_token, self.bob_raw = ApiToken.issue(self.bob, "test")

        self.client = Client()
        self.cat = Category.objects.create(name="随笔", author=self.alice)
        self.tag = Tag.objects.create(name="生活", author=self.alice)
        self.published = Post.objects.create(
            title="公开文章", body="# 标题\n\n正文内容", author=self.alice,
            status="published", category=self.cat,
        )
        self.published.tags.add(self.tag)
        self.draft = Post.objects.create(
            title="草稿文章", body="草稿正文", author=self.alice, status="draft",
        )
        self.public_memo = Memo.objects.create(
            content="公开碎碎念", author=self.alice, is_public=True,
        )
        self.private_memo = Memo.objects.create(
            content="私密碎碎念", author=self.alice, is_public=False,
        )

    def auth(self, raw_token):
        return {"HTTP_AUTHORIZATION": f"Bearer {raw_token}"}

    def json(self, response):
        self.assertTrue(response["Content-Type"].startswith("application/json"),
                        f"expected JSON, got {response['Content-Type']}: {response.content[:200]!r}")
        return response.json()

    # ── 便于新端点测试的 JSON 帮助方法 ──────────────────────────────────

    def _headers(self, token):
        return self.auth(token) if token else {}

    def get_json(self, url, token=None):
        return self.client.get(url, **self._headers(token))

    def post_json(self, url, data=None, token=None):
        return self.client.post(url, data=json.dumps(data or {}),
                                content_type="application/json", **self._headers(token))

    def patch_json(self, url, data=None, token=None):
        return self.client.patch(url, data=json.dumps(data or {}),
                                 content_type="application/json", **self._headers(token))

    def delete_json(self, url, token=None):
        return self.client.delete(url, **self._headers(token))


class MetaApiTests(ApiTestCase):
    def test_health(self):
        data = self.json(self.client.get("/api/v1/health"))
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["database"], "ok")

    def test_meta_counts_public_only(self):
        data = self.json(self.client.get("/api/v1/meta"))
        self.assertEqual(data["totals"]["posts"], 1)
        self.assertEqual(data["totals"]["memos"], 1)
        self.assertEqual(data["auth"]["token_prefix"], "mlc_")

    def test_openapi_schema_served(self):
        response = self.client.get("/api/v1/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/posts", response.json()["paths"])

    def test_docs_page(self):
        self.assertEqual(self.client.get("/api/v1/docs").status_code, 200)


    def test_committed_schema_snapshot_has_no_drift(self):
        """``docs/openapi.json`` 是随仓库发布的快照：路径集合必须和代码一致。

        改了端点却忘了重跑 ``manage.py export_openapi`` 时，这条会红。
        """
        from pathlib import Path
        snapshot = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
        self.assertTrue(snapshot.exists(), "请先跑 manage.py export_openapi")
        committed = json.loads(snapshot.read_text(encoding="utf-8"))
        live = api.get_openapi_schema(path_prefix="api/v1")
        self.assertEqual(set(committed["paths"]), set(live["paths"]))
        for path, item in live["paths"].items():
            self.assertEqual(set(committed["paths"][path]), set(item),
                             f"{path} 的方法集合变了")

class TokenAuthTests(ApiTestCase):
    def test_token_plain_text_only_at_issue(self):
        self.assertTrue(self.alice_raw.startswith("mlc_"))
        self.assertEqual(len(self.alice_raw), 4 + 40)
        self.assertNotIn(self.alice_raw, ApiToken.objects.get(pk=self.alice_token.pk).token_hash)

    def test_missing_token_rejected(self):
        response = self.client.post("/api/v1/memos",
                                    data={"content": "x"}, content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.json(response)["error"]["code"], "unauthorized")

    def test_bad_token_rejected(self):
        response = self.client.get("/api/v1/posts", **self.auth("mlc_" + "0" * 40))
        self.assertEqual(response.status_code, 401)

    def test_revoked_token_rejected(self):
        from django.utils import timezone
        ApiToken.objects.filter(pk=self.alice_token.pk).update(revoked_at=timezone.now())
        response = self.client.get("/api/v1/memos", **self.auth(self.alice_raw))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.json(response)["error"]["code"], "unauthorized")

    def test_banned_user_rejected(self):
        self.alice.profile.is_banned = True
        self.alice.profile.save(update_fields=["is_banned"])
        response = self.client.get("/api/v1/memos", **self.auth(self.alice_raw))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.json(response)["error"]["code"], "forbidden")

    def test_last_used_at_updated(self):
        self.assertIsNone(self.alice_token.last_used_at)
        self.client.get("/api/v1/posts", **self.auth(self.alice_raw))
        self.alice_token.refresh_from_db()
        self.assertIsNotNone(self.alice_token.last_used_at)


class PostReadApiTests(ApiTestCase):
    def test_list_only_published_for_anonymous(self):
        data = self.json(self.client.get("/api/v1/posts"))
        self.assertEqual(data["count"], 1)
        titles = [item["title"] for item in data["results"]]
        self.assertEqual(titles, ["公开文章"])

    def test_list_mine_includes_drafts(self):
        data = self.json(self.client.get("/api/v1/posts?mine=true", **self.auth(self.alice_raw)))
        self.assertEqual(data["count"], 2)

    def test_list_mine_requires_token(self):
        response = self.client.get("/api/v1/posts?mine=true")
        self.assertEqual(response.status_code, 403)

    def test_list_mine_status_filter(self):
        data = self.json(self.client.get(
            "/api/v1/posts?mine=true&status=draft", **self.auth(self.alice_raw)))
        self.assertEqual([item["title"] for item in data["results"]], ["草稿文章"])

    def test_list_rejects_bad_status(self):
        response = self.client.get("/api/v1/posts?mine=true&status=nope",
                                   **self.auth(self.alice_raw))
        self.assertEqual(response.status_code, 422)

    def test_detail_by_unique_id(self):
        data = self.json(self.client.get(f"/api/v1/posts/{self.published.unique_id}"))
        self.assertEqual(data["title"], "公开文章")
        self.assertIn("<h1", data["body_html"])
        self.assertEqual(data["category"]["name"], "随笔")
        self.assertEqual([t["name"] for t in data["tags"]], ["生活"])
        # 绝对 URL，中文 slug 会做 IRI 编码，所以只校验作者段与编码后的结尾
        self.assertIn("/@alice/", data["url"])
        self.assertTrue(data["url"].endswith(quote(self.published.slug) + "/"), data["url"])
        self.assertEqual(data["likes"], {"count": 0, "display_text": "", "user_liked": False})
        self.assertEqual(data["comments_count"], 0)
        self.assertIsNone(data["series_nav"])

    def test_detail_by_slug(self):
        data = self.json(self.client.get(f"/api/v1/posts/{self.published.slug}"))
        self.assertEqual(data["unique_id"], str(self.published.unique_id))

    def test_draft_not_readable_anonymously(self):
        response = self.client.get(f"/api/v1/posts/{self.draft.unique_id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.json(response)["error"]["code"], "not_found")

    def test_draft_readable_by_author(self):
        response = self.client.get(f"/api/v1/posts/{self.draft.unique_id}",
                                   **self.auth(self.alice_raw))
        self.assertEqual(response.status_code, 200)

    def test_slug_ambiguous_returns_409(self):
        Post.objects.create(title="公开文章", body="x", author=self.bob, status="published")
        response = self.client.get("/api/v1/posts/公开文章")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.json(response)["error"]["code"], "ambiguous_slug")
        # 加作者即可消歧
        ok = self.client.get("/api/v1/posts/公开文章?author=alice")
        self.assertEqual(ok.status_code, 200)

    def test_filters(self):
        self.assertEqual(self.json(self.client.get("/api/v1/posts?author=alice"))["count"], 1)
        self.assertEqual(self.json(self.client.get("/api/v1/posts?author=bob"))["count"], 0)
        cat_slug = quote(self.cat.slug)
        self.assertEqual(self.json(self.client.get(f"/api/v1/posts?category={cat_slug}"))["count"], 1)
        self.assertEqual(self.json(self.client.get("/api/v1/posts?tag=生活"))["count"], 1)
        self.assertEqual(self.json(self.client.get("/api/v1/posts?series=nope"))["count"], 0)
        self.assertEqual(self.json(self.client.get("/api/v1/posts?q=公开"))["count"], 1)
        self.assertEqual(self.json(self.client.get("/api/v1/posts?q=不存在的关键字"))["count"], 0)

    def test_pagination(self):
        for i in range(5):
            Post.objects.create(title=f"批量 {i}", body="内容", author=self.alice,
                                status="published")
        page1 = self.json(self.client.get("/api/v1/posts?page_size=3"))
        self.assertEqual(page1["count"], 6)
        self.assertEqual(page1["total_pages"], 2)
        self.assertEqual(len(page1["results"]), 3)
        page2 = self.json(self.client.get("/api/v1/posts?page_size=3&page=2"))
        self.assertEqual(len(page2["results"]), 3)
        self.assertEqual({r["title"] for r in page1["results"]} ^ {r["title"] for r in page2["results"]},
                         {r["title"] for r in page1["results"]} | {r["title"] for r in page2["results"]})

    def test_page_size_clamped(self):
        data = self.json(self.client.get("/api/v1/posts?page_size=9999"))
        self.assertLessEqual(data["page_size"], 100)


class PostWriteApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.bob_cat = Category.objects.create(name="bob 的分类", author=self.bob)

    def test_create_post(self):
        response = self.client.post(
            "/api/v1/posts",
            data={"title": "新文章", "body": "正文", "status": "published",
                  "category_id": self.bob_cat.pk, "tag_names": ["API", "测试"]},
            content_type="application/json", **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 201, response.content)
        data = self.json(response)
        self.assertEqual(data["status"], "published")
        self.assertEqual(data["category"]["name"], "bob 的分类")
        self.assertEqual(sorted(t["name"] for t in data["tags"]), ["API", "测试"])
        post = Post.objects.get(title="新文章")
        self.assertEqual(post.author, self.bob)
        self.assertTrue(post.excerpt)  # 自动生成

    def test_create_rejects_other_authors_category(self):
        """分类/标签按作者隔离，移动端不能把文章挂到别人的分类上。"""
        response = self.client.post(
            "/api/v1/posts",
            data={"title": "越权", "body": "正文", "category_id": self.cat.pk},
            content_type="application/json", **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(self.json(response)["details"]["category"][0].split("：")[0],
                         "选择一个有效的选项")
        self.assertFalse(Post.objects.filter(title="越权").exists())

    def test_create_requires_token(self):
        response = self.client.post("/api/v1/posts", data={"title": "x", "body": "y"},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_create_validates_body_required(self):
        response = self.client.post("/api/v1/posts", data={"title": "只有标题"},
                                    content_type="application/json", **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 422)
        data = self.json(response)
        self.assertEqual(data["error"]["code"], "validation_error")
        self.assertIn("body", data["details"])

    def test_create_rejects_bad_license(self):
        response = self.client.post(
            "/api/v1/posts",
            data={"title": "许可", "body": "内容", "license": "MIT"},
            content_type="application/json", **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 422)

    def test_patch_partial_update(self):
        response = self.client.patch(
            f"/api/v1/posts/{self.published.unique_id}",
            data={"excerpt": "手工摘要"}, content_type="application/json",
            **self.auth(self.alice_raw))
        self.assertEqual(response.status_code, 200, response.content)
        data = self.json(response)
        self.assertEqual(data["excerpt"], "手工摘要")
        self.assertEqual(data["title"], "公开文章")
        self.assertEqual([t["name"] for t in data["tags"]], ["生活"])

    def test_patch_rejects_other_author(self):
        response = self.client.patch(
            f"/api/v1/posts/{self.published.unique_id}",
            data={"title": "篡改"}, content_type="application/json",
            **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 403)

    def test_patch_series_order(self):
        response = self.client.patch(
            f"/api/v1/posts/{self.published.unique_id}",
            data={"series_order": 7}, content_type="application/json",
            **self.auth(self.alice_raw))
        self.assertEqual(response.status_code, 200, response.content)
        self.published.refresh_from_db()
        self.assertEqual(self.published.series_order, 7)

    def test_publish_endpoint(self):
        self.assertEqual(self.draft.status, "draft")
        response = self.client.post(f"/api/v1/posts/{self.draft.unique_id}/publish",
                                    **self.auth(self.alice_raw))
        self.assertEqual(response.status_code, 200, response.content)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, "published")

    def test_delete_post(self):
        response = self.client.delete(f"/api/v1/posts/{self.draft.unique_id}",
                                      **self.auth(self.alice_raw))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.json(response)["ok"])
        self.assertFalse(Post.objects.filter(pk=self.draft.pk).exists())

    def test_delete_forbidden_for_other_user(self):
        response = self.client.delete(f"/api/v1/posts/{self.published.unique_id}",
                                      **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Post.objects.filter(pk=self.published.pk).exists())


class MemoApiTests(ApiTestCase):
    def test_anonymous_sees_only_public(self):
        data = self.json(self.client.get("/api/v1/memos"))
        self.assertEqual([m["content"] for m in data["results"]], ["公开碎碎念"])

    def test_author_sees_own_private(self):
        contents = {m["content"] for m in
                    self.json(self.client.get("/api/v1/memos", **self.auth(self.alice_raw)))["results"]}
        self.assertEqual(contents, {"公开碎碎念", "私密碎碎念"})

    def test_other_user_cannot_see_private(self):
        contents = {m["content"] for m in
                    self.json(self.client.get("/api/v1/memos", **self.auth(self.bob_raw)))["results"]}
        self.assertNotIn("私密碎碎念", contents)

    def test_private_memo_detail_hidden(self):
        response = self.client.get(f"/api/v1/memos/{self.private_memo.pk}")
        self.assertEqual(response.status_code, 404)
        ok = self.client.get(f"/api/v1/memos/{self.private_memo.pk}", **self.auth(self.alice_raw))
        self.assertEqual(ok.status_code, 200)

    def test_create_and_delete(self):
        response = self.client.post("/api/v1/memos", data={"content": "移动端发的", "is_public": False},
                                    content_type="application/json", **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 201, response.content)
        memo_id = self.json(response)["id"]
        self.assertFalse(Memo.objects.get(pk=memo_id).is_public)
        deleted = self.client.delete(f"/api/v1/memos/{memo_id}", **self.auth(self.bob_raw))
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Memo.objects.filter(pk=memo_id).exists())

    def test_empty_memo_rejected(self):
        response = self.client.post("/api/v1/memos", data={"content": ""},
                                    content_type="application/json", **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 422)

    def test_delete_other_memo_forbidden(self):
        response = self.client.delete(f"/api/v1/memos/{self.public_memo.pk}",
                                      **self.auth(self.bob_raw))
        self.assertEqual(response.status_code, 403)


class OfflineGuestAvatarMixin:
    """游客头像会真去 api.dicebear.com 拉图；测试里一律打桩，避免依赖外网。"""

    def setUp(self):
        from unittest.mock import patch
        for target in ("blog.models.get_or_create_guest_avatar",
                       "blog.views.get_or_create_guest_avatar"):
            patcher = patch(target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()


class TaxonomyApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.bob_cat = Category.objects.create(name="bob 的分类", author=self.bob)

    def test_lists_only_items_with_visible_posts(self):
        names = [c["name"] for c in self.json(self.client.get("/api/v1/categories"))["results"]]
        self.assertEqual(names, ["随笔"])

    def test_own_unused_item_visible_with_token(self):
        data = self.json(self.get_json("/api/v1/categories", self.alice_raw))
        self.assertEqual([c["name"] for c in data["results"]], ["随笔"])
        data = self.json(self.get_json("/api/v1/categories", self.bob_raw))
        # bob 的空分类（本人可见）+ alice 挂着已发布文章的分类（人人可见），按名称排序
        self.assertEqual([(c["name"], c["post_count"]) for c in data["results"]],
                         [("bob 的分类", 0), ("随笔", 1)])

    def test_post_count(self):
        data = self.json(self.client.get("/api/v1/categories"))
        self.assertEqual(data["results"][0]["post_count"], 1)

    def test_filter_by_author(self):
        self.assertEqual(self.json(self.client.get("/api/v1/categories?author=alice"))["count"], 1)
        self.assertEqual(self.json(self.client.get("/api/v1/categories?author=bob"))["count"], 0)

    def test_tag_listing(self):
        data = self.json(self.client.get("/api/v1/tags"))
        self.assertEqual([t["name"] for t in data["results"]], ["生活"])

    def test_create_category_requires_token(self):
        self.assertEqual(self.post_json("/api/v1/categories", {"name": "新"}).status_code, 401)

    def test_create_category_is_idempotent(self):
        first = self.post_json("/api/v1/categories", {"name": "技术"}, self.alice_raw)
        self.assertEqual(first.status_code, 201, first.content)
        again = self.post_json("/api/v1/categories", {"name": "技术"}, self.alice_raw)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.json(again)["id"], self.json(first)["id"])
        self.assertEqual(Category.objects.filter(name="技术").count(), 1)

    def test_create_category_blank_name_rejected(self):
        response = self.post_json("/api/v1/categories", {"name": "   "}, self.alice_raw)
        self.assertEqual(response.status_code, 422)

    def test_created_category_is_author_scoped(self):
        self.post_json("/api/v1/categories", {"name": "技术"}, self.bob_raw)
        names = self.json(self.get_json("/api/v1/categories", self.alice_raw))
        self.assertNotIn("技术", [c["name"] for c in names["results"]])

    def test_create_tag(self):
        response = self.post_json("/api/v1/tags", {"name": "移动端"}, self.alice_raw)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Tag.objects.filter(name="移动端", author=self.alice).exists())

    def test_series_create_and_list(self):
        created = self.post_json("/api/v1/series", {"name": "日记本", "description": "每天一点"},
                                 self.alice_raw)
        self.assertEqual(created.status_code, 201, created.content)
        series_id = self.json(created)["id"]
        again = self.post_json("/api/v1/series", {"name": "日记本"}, self.alice_raw)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.json(again)["id"], series_id)

        listing = self.json(self.get_json("/api/v1/series", self.alice_raw))
        self.assertEqual([s["name"] for s in listing["results"]], ["日记本"])
        self.assertEqual(listing["results"][0]["description"], "每天一点")

    def test_series_detail_and_posts(self):
        from .models import Series
        series = Series.objects.create(name="系列", author=self.alice)
        second = Post.objects.create(title="第二篇", body="B", author=self.alice,
                                    status="published", series=series, series_order=2)
        self.published.series = series
        self.published.series_order = 1
        self.published.save(update_fields=["series", "series_order"])

        detail = self.json(self.client.get(f"/api/v1/series/{series.pk}"))
        self.assertEqual(detail["post_count"], 2)
        self.assertEqual(detail["author"], "alice")

        posts = self.json(self.client.get(f"/api/v1/series/{series.pk}/posts"))
        self.assertEqual([p["title"] for p in posts["results"]], ["公开文章", "第二篇"])
        # 列表项与文章详情端点给出同一个 url，移动端可直接跳转
        first = self.json(self.client.get(f"/api/v1/posts/{self.published.unique_id}"))
        second_detail = self.json(self.client.get(f"/api/v1/posts/{second.unique_id}"))
        self.assertEqual(posts["results"][0]["url"], first["url"])
        self.assertEqual(posts["results"][1]["url"], second_detail["url"])

    def test_series_of_other_author_without_published_posts_is_404(self):
        from .models import Series
        series = Series.objects.create(name="别人的系列", author=self.bob)
        response = self.client.get(f"/api/v1/series/{series.pk}")
        self.assertEqual(response.status_code, 404)
        ok = self.get_json(f"/api/v1/series/{series.pk}", self.bob_raw)
        self.assertEqual(ok.status_code, 200)

    def test_add_post_to_series(self):
        from .models import Series
        series = Series.objects.create(name="我的系列", author=self.alice)
        response = self.post_json(f"/api/v1/series/{series.pk}/posts",
                                  {"action": "add", "post_id": self.draft.pk}, self.alice_raw)
        self.assertEqual(response.status_code, 200, response.content)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.series_id, series.pk)

        removed = self.post_json(f"/api/v1/series/{series.pk}/posts",
                                 {"action": "remove", "post_id": self.draft.pk}, self.alice_raw)
        self.assertEqual(removed.status_code, 200)
        self.draft.refresh_from_db()
        self.assertIsNone(self.draft.series)
        self.assertEqual(self.draft.series_order, 1)

    def test_add_other_authors_post_to_series_is_404(self):
        from .models import Series
        series = Series.objects.create(name="bob 的系列", author=self.bob)
        response = self.post_json(f"/api/v1/series/{series.pk}/posts",
                                  {"action": "add", "post_id": self.published.pk}, self.bob_raw)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.json(response)["error"]["code"], "not_found")

    def test_series_manage_bad_action(self):
        from .models import Series
        series = Series.objects.create(name="系列", author=self.alice)
        response = self.post_json(f"/api/v1/series/{series.pk}/posts",
                                  {"action": "nope", "post_id": self.published.pk}, self.alice_raw)
        self.assertEqual(response.status_code, 400)


class DiscoveryApiTests(ApiTestCase):
    def test_users_listed_with_stats(self):
        data = self.json(self.client.get("/api/v1/users"))
        self.assertEqual(data["count"], 2)
        alice = [u for u in data["results"] if u["username"] == "alice"][0]
        self.assertEqual(alice["stats"]["published_count"], 1)
        self.assertEqual(alice["stats"]["total_posts"], 2)
        self.assertEqual(alice["stats"]["public_memos"], 1)

    def test_users_search_and_order(self):
        data = self.json(self.client.get("/api/v1/users?q=ali"))
        self.assertEqual([u["username"] for u in data["results"]], ["alice"])
        data = self.json(self.client.get("/api/v1/users?order=username"))
        self.assertEqual([u["username"] for u in data["results"]], ["alice", "bob"])

    def test_user_detail(self):
        data = self.json(self.client.get("/api/v1/users/alice"))
        self.assertEqual(data["display_name"], "alice")
        self.assertIn("/@alice/", data["url"])

    def test_unknown_or_banned_user_is_404(self):
        self.assertEqual(self.client.get("/api/v1/users/nobody").status_code, 404)
        self.bob.profile.is_banned = True
        self.bob.profile.save(update_fields=["is_banned"])
        self.assertEqual(self.client.get("/api/v1/users/bob").status_code, 404)

    def test_archives_group_by_month(self):
        data = self.json(self.client.get("/api/v1/archives"))
        self.assertEqual(data["count"], 1)
        bucket = data["buckets"][0]
        self.assertEqual(bucket["count"], 1)
        self.assertEqual(bucket["posts"][0]["title"], "公开文章")
        self.assertIn("年", bucket["label"])

    def test_archives_filters(self):
        self.assertEqual(self.json(self.client.get("/api/v1/archives?author=bob"))["count"], 0)
        self.assertEqual(self.json(self.client.get("/api/v1/archives?year=1999"))["count"], 0)
        from django.utils import timezone
        now = timezone.localtime(self.published.created_time)
        ok = self.json(self.client.get(f"/api/v1/archives?year={now.year}&month={now.month}"))
        self.assertEqual(ok["count"], 1)

    def test_search_matches_posts_and_memos(self):
        data = self.json(self.client.get("/api/v1/search?q=公开"))
        self.assertEqual([p["title"] for p in data["posts"]], ["公开文章"])
        self.assertEqual([m["content"] for m in data["memos"]], ["公开碎碎念"])
        self.assertEqual(data["query"], "公开")

    def test_search_empty_query_returns_nothing(self):
        data = self.json(self.client.get("/api/v1/search"))
        self.assertEqual({k: v for k, v in data.items() if k != "query"},
                         {"posts": [], "memos": [], "users": [], "categories": [],
                          "tags": [], "series": []})

    def test_search_respects_visibility(self):
        self.assertEqual(self.json(self.client.get("/api/v1/search?q=私密"))["memos"], [])
        data = self.json(self.get_json("/api/v1/search?q=私密", self.alice_raw))
        self.assertEqual([m["content"] for m in data["memos"]], ["私密碎碎念"])
        # 别人的私密内容仍然看不到
        self.assertEqual(self.json(
            self.get_json("/api/v1/search?q=私密", self.bob_raw))["memos"], [])

    def test_search_type_filter(self):
        data = self.json(self.client.get("/api/v1/search?q=alice&types=user"))
        self.assertEqual([u["username"] for u in data["users"]], ["alice"])
        self.assertEqual(data["posts"], [])

    def test_search_taxonomy(self):
        data = self.json(self.client.get("/api/v1/search?q=随笔&types=category"))
        self.assertEqual([c["name"] for c in data["categories"]], ["随笔"])


class SocialApiTests(OfflineGuestAvatarMixin, ApiTestCase):
    def post_key(self):
        return {"content_type": "blog.post", "object_id": self.published.pk}

    def test_list_comments_visible_only(self):
        Comment.objects.create(user=self.bob, content_type=self.ct(), object_id=self.published.pk,
                              content="已过审", is_visible=True)
        hidden = Comment.objects.create(
            guest_email="x@example.com", guest_name="游客",
            content_type=self.ct(), object_id=self.published.pk,
            content="待审核", is_visible=False)
        data = self.json(self.client.get("/api/v1/comments"))
        self.assertEqual([c["content"] for c in data["results"]], ["已过审"])
        # 管理员能看到全部
        self.bob.is_staff = True
        self.bob.save(update_fields=["is_staff"])
        data = self.json(self.get_json("/api/v1/comments", self.bob_raw))
        self.assertIn(hidden.pk, [c["id"] for c in data["results"]])

    def ct(self):
        from django.contrib.contenttypes.models import ContentType
        return ContentType.objects.get_for_model(Post)

    def test_filter_comments_by_target(self):
        Comment.objects.create(user=self.bob, content_type=self.ct(),
                               object_id=self.published.pk, content="这篇文章的评论")
        Comment.objects.create(user=self.bob, content_type=self.ct(),
                               object_id=self.draft.pk, content="草稿的评论")
        url = f"/api/v1/comments?content_type=blog.post&object_id={self.published.pk}"
        data = self.json(self.client.get(url))
        self.assertEqual([c["content"] for c in data["results"]], ["这篇文章的评论"])

    def test_comments_on_unpublished_target_forbidden(self):
        url = f"/api/v1/comments?content_type=blog.post&object_id={self.draft.pk}"
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_create_comment_as_user_notifies_author(self):
        response = self.post_json("/api/v1/comments",
                                  {**self.post_key(), "content": "写得好"}, self.bob_raw)
        self.assertEqual(response.status_code, 201, response.content)
        data = self.json(response)
        self.assertFalse(data["is_guest"])
        self.assertTrue(data["is_visible"])
        self.assertEqual(Comment.objects.filter(content="写得好").count(), 1)
        note = Notification.objects.get(recipient=self.alice)
        self.assertEqual(note.notification_type, "comment")
        self.assertIn("bob", note.message)

    def test_comment_on_own_post_no_notification(self):
        self.post_json("/api/v1/comments", {**self.post_key(), "content": "自己的话"},
                       self.alice_raw)
        self.assertEqual(Notification.objects.count(), 0)

    def test_guest_comment_requires_name_and_email(self):
        response = self.post_json("/api/v1/comments", {**self.post_key(), "content": "游客评论"})
        self.assertEqual(response.status_code, 422)
        details = self.json(response)["details"]
        self.assertIn("guest_name", details)
        self.assertIn("guest_email", details)

    def test_guest_first_comment_is_pending(self):
        response = self.post_json("/api/v1/comments",
                                  {**self.post_key(), "content": "游客首评",
                                   "guest_name": "小明", "guest_email": "x@example.com"})
        self.assertEqual(response.status_code, 202, response.content)
        data = self.json(response)
        self.assertTrue(data["is_guest"])
        self.assertTrue(data["is_pending"])
        # 待审评论不会出现在公开列表里
        listing = self.json(self.client.get(
            f"/api/v1/comments?content_type=blog.post&object_id={self.published.pk}"))
        self.assertEqual(listing["results"], [])

    def test_guest_becomes_trusted_after_approval(self):
        first = self.post_json("/api/v1/comments",
                               {**self.post_key(), "content": "游客首评",
                                "guest_name": "小明", "guest_email": "y@example.com"})
        comment_id = self.json(first)["id"]
        self.staff = self.bob
        self.bob.is_staff = True
        self.bob.save(update_fields=["is_staff"])
        approved = self.post_json(f"/api/v1/comments/{comment_id}/visibility", {}, self.bob_raw)
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertTrue(self.json(approved)["is_visible"])

        second = self.post_json("/api/v1/comments",
                                {**self.post_key(), "content": "游客二评",
                                 "guest_name": "小明", "guest_email": "y@example.com"})
        self.assertEqual(second.status_code, 201)
        self.assertFalse(self.json(second)["is_pending"])

    def test_honeypot_rejected(self):
        response = self.post_json("/api/v1/comments",
                                  {**self.post_key(), "content": "机器人", "website": "http://spam"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.json(response)["error"]["code"], "spam_detected")
        self.assertEqual(Comment.objects.filter(content="机器人").count(), 0)

    def test_comment_content_validated(self):
        response = self.post_json("/api/v1/comments", {**self.post_key(), "content": "短"},
                                  self.bob_raw)
        self.assertEqual(response.status_code, 422)

    def test_duplicate_comment_rejected(self):
        payload = {**self.post_key(), "content": "同一句话"}
        self.assertEqual(self.post_json("/api/v1/comments", payload, self.bob_raw).status_code, 201)
        second = self.post_json("/api/v1/comments", payload, self.bob_raw)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.json(second)["error"]["code"], "duplicate")

    def test_comment_rate_limit(self):
        for i in range(5):
            response = self.post_json("/api/v1/comments",
                                      {**self.post_key(), "content": f"刷第 {i} 条"}, self.bob_raw)
            self.assertEqual(response.status_code, 201, response.content)
        blocked = self.post_json("/api/v1/comments",
                                 {**self.post_key(), "content": "刷第 6 条"}, self.bob_raw)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(self.json(blocked)["error"]["code"], "throttled")
        self.assertIn("Retry-After", blocked.headers)

    def test_invalid_target(self):
        response = self.post_json("/api/v1/comments",
                                  {"content_type": "blog.user", "object_id": 1, "content": "越权"},
                                  self.bob_raw)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.json(response)["error"]["code"], "invalid_content_type")

    def test_visibility_toggle_requires_staff(self):
        comment = Comment.objects.create(user=self.bob, content_type=self.ct(),
                                         object_id=self.published.pk, content="普通评论")
        denied = self.post_json(f"/api/v1/comments/{comment.pk}/visibility", {}, self.alice_raw)
        self.assertEqual(denied.status_code, 403)
        comment.refresh_from_db()
        self.assertTrue(comment.is_visible)

    def test_like_toggle_creates_notification_once(self):
        response = self.post_json("/api/v1/likes", self.post_key(), self.bob_raw)
        self.assertEqual(response.status_code, 200, response.content)
        data = self.json(response)
        self.assertEqual(data, {"liked": True, "count": 1, "display_text": "bob 赞了"})
        self.assertEqual(Notification.objects.filter(recipient=self.alice,
                                                     notification_type="like").count(), 1)
        # 详情页里的点赞状态同步
        detail = self.json(self.get_json(f"/api/v1/posts/{self.published.unique_id}", self.bob_raw))
        self.assertEqual(detail["likes"], {"count": 1, "display_text": "bob 赞了",
                                           "user_liked": True})

        toggle_off = self.post_json("/api/v1/likes", self.post_key(), self.bob_raw)
        self.assertFalse(self.json(toggle_off)["liked"])
        self.assertEqual(Notification.objects.count(), 1)

    def test_like_own_content_no_notification(self):
        response = self.post_json("/api/v1/likes", self.post_key(), self.alice_raw)
        self.assertTrue(self.json(response)["liked"])
        self.assertEqual(Notification.objects.count(), 0)

    def test_like_requires_login(self):
        self.assertEqual(self.post_json("/api/v1/likes", self.post_key()).status_code, 401)

    def test_like_unpublished_forbidden(self):
        response = self.post_json("/api/v1/likes",
                                  {"content_type": "blog.post", "object_id": self.draft.pk},
                                  self.bob_raw)
        self.assertEqual(response.status_code, 403)

    def test_like_state_batch(self):
        self.post_json("/api/v1/likes", self.post_key(), self.bob_raw)
        targets = f"blog.post:{self.published.pk},blog.memo:{self.public_memo.pk},junk,blog.post:abc"
        data = self.json(self.get_json(f"/api/v1/likes?targets={quote(targets)}", self.bob_raw))
        self.assertEqual(sorted(data["states"]), ["blog.memo:%d" % self.public_memo.pk,
                                                 "blog.post:%d" % self.published.pk])
        self.assertTrue(data["states"][f"blog.post:{self.published.pk}"]["user_liked"])
        self.assertEqual(data["states"][f"blog.memo:{self.public_memo.pk}"]["count"], 0)

    def test_like_state_batch_skips_invisible(self):
        data = self.json(self.get_json(
            f"/api/v1/likes?targets=blog.post:{self.draft.pk}", self.bob_raw))
        self.assertEqual(data["states"], {})


class InboxApiTests(ApiTestCase):
    def make_note(self, recipient, actor=None):
        from django.contrib.contenttypes.models import ContentType
        return Notification.objects.create(
            recipient=recipient, actor=actor, notification_type="comment",
            message="有人评论了你", content_type=ContentType.objects.get_for_model(Post),
            object_id=1)

    def test_requires_auth(self):
        self.assertEqual(self.client.get("/api/v1/inbox").status_code, 401)

    def test_list_marks_target_url_and_unread(self):
        self.make_note(self.bob, self.alice)
        data = self.json(self.get_json("/api/v1/inbox", self.bob_raw))
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["unread"], 1)
        note = data["results"][0]
        self.assertEqual(note["type_display"], "评论")
        self.assertEqual(note["actor"]["username"], "alice")
        self.assertIn("/@alice/", note["target_url"])

    def test_others_cannot_see_my_notes(self):
        self.make_note(self.bob)
        self.assertEqual(self.json(self.get_json("/api/v1/inbox", self.alice_raw))["count"], 0)

    def test_filter_unread_and_type(self):
        note = self.make_note(self.bob)
        note.is_read = True
        note.save(update_fields=["is_read"])
        self.assertEqual(self.json(self.get_json("/api/v1/inbox?unread_only=true",
                                                 self.bob_raw))["count"], 0)
        self.assertEqual(self.json(self.get_json("/api/v1/inbox?type=like",
                                                 self.bob_raw))["count"], 0)

    def test_unread_endpoint(self):
        self.make_note(self.bob)
        self.assertEqual(self.json(self.get_json("/api/v1/inbox/unread", self.bob_raw))["unread"], 1)

    def test_mark_specific_read(self):
        note = self.make_note(self.bob)
        response = self.post_json("/api/v1/inbox/read", {"ids": [note.pk]}, self.bob_raw)
        self.assertEqual(self.json(response), {"updated": 1, "unread": 0})
        note.refresh_from_db()
        self.assertTrue(note.is_read)

    def test_mark_other_users_note_is_noop(self):
        note = self.make_note(self.bob)
        response = self.post_json("/api/v1/inbox/read", {"ids": [note.pk]}, self.alice_raw)
        self.assertEqual(self.json(response)["updated"], 0)

    def test_mark_all_read(self):
        for _ in range(2):
            self.make_note(self.bob)
        response = self.post_json("/api/v1/inbox/read", {"mark_all": True}, self.bob_raw)
        self.assertEqual(self.json(response), {"updated": 2, "unread": 0})

    def test_mark_read_needs_ids_or_flag(self):
        self.assertEqual(self.post_json("/api/v1/inbox/read", {}, self.bob_raw).status_code, 422)


class AccountApiTests(ApiTestCase):
    def test_login_returns_usable_token(self):
        response = self.post_json("/api/v1/auth/login",
                                  {"username": "alice", "password": "pw-alice",
                                   "device_name": "pixel"})
        self.assertEqual(response.status_code, 200, response.content)
        data = self.json(response)
        self.assertTrue(data["token"].startswith("mlc_"))
        self.assertEqual(data["name"], "pixel")
        self.assertEqual(data["user"]["username"], "alice")
        mine = self.json(self.get_json("/api/v1/posts?mine=true", data["token"]))
        self.assertEqual(mine["count"], 2)

    def test_login_wrong_password(self):
        response = self.post_json("/api/v1/auth/login",
                                  {"username": "alice", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.json(response)["error"]["code"], "invalid_credentials")

    def test_login_rate_limited(self):
        """连续失败到达阈值后连正确密码也拒绝，并给出 Retry-After。"""
        for _ in range(LOGIN_LIMIT):
            self.post_json("/api/v1/auth/login", {"username": "alice", "password": "bad"})
        blocked = self.post_json("/api/v1/auth/login",
                                 {"username": "alice", "password": "pw-alice"})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(self.json(blocked)["error"]["code"], "throttled")
        retry = int(blocked.headers["Retry-After"])
        self.assertLessEqual(retry, LOGIN_WINDOW)
        self.assertGreater(retry, 0)

    def test_login_banned_user(self):
        self.alice.profile.is_banned = True
        self.alice.profile.save(update_fields=["is_banned"])
        response = self.post_json("/api/v1/auth/login",
                                  {"username": "alice", "password": "pw-alice"})
        self.assertEqual(response.status_code, 403)

    def test_login_requires_both_fields(self):
        self.assertEqual(self.post_json("/api/v1/auth/login",
                                         {"username": "alice", "password": ""}).status_code, 422)

    def test_register_with_invite(self):
        from .models import InviteCode
        from django.utils import timezone
        code = InviteCode.objects.create(code="abc123", inviter=self.alice,
                                        expires_at=timezone.now() + timezone.timedelta(hours=1))
        response = self.post_json("/api/v1/auth/register",
                                  {"code": "abc123", "username": "carol",
                                   "password1": "Zq!8mopw-v3", "password2": "Zq!8mopw-v3"})
        self.assertEqual(response.status_code, 201, response.content)
        data = self.json(response)
        self.assertEqual(data["user"]["username"], "carol")
        code.refresh_from_db()
        self.assertTrue(code.is_used)
        self.assertEqual(code.invitee.username, "carol")
        self.assertEqual(self.get_json("/api/v1/auth/me", data["token"]).status_code, 200)

    def test_register_invite_errors(self):
        """邀请码缺失 / 已用 / 已过期分别对应 404 / 409 / 410。"""
        from .models import InviteCode
        from django.utils import timezone
        now_plus = timezone.now() + timezone.timedelta(hours=1)
        used = InviteCode.objects.create(code="used", inviter=self.alice, is_used=True,
                                        invitee=self.bob, expires_at=now_plus)
        expired = InviteCode.objects.create(code="old", inviter=self.alice,
                                           expires_at=timezone.now() - timezone.timedelta(minutes=1))
        body = {"username": "dave", "password1": "Zq!8mopw-v3", "password2": "Zq!8mopw-v3"}

        for code, status, err_code in (("nope", 404, "invite_not_found"),
                                       (used.code, 409, "invite_used"),
                                       (expired.code, 410, "invite_expired")):
            response = self.post_json("/api/v1/auth/register", {**body, "code": code})
            self.assertEqual((response.status_code, self.json(response)["error"]["code"]),
                             (status, err_code))
        self.assertFalse(User.objects.filter(username="dave").exists())

    def test_register_password_rules_come_from_django_form(self):
        """口令规则用 Django 的 UserCreationForm：弱口令 / 两次不一致 → 422 且不建用户。"""
        from .models import InviteCode
        from django.utils import timezone
        invite = InviteCode.objects.create(code="good", inviter=self.alice,
                                           expires_at=timezone.now() + timezone.timedelta(hours=1))
        response = self.post_json("/api/v1/auth/register",
                                  {"code": "good", "username": "dave",
                                   "password1": "weak", "password2": "weak"})
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(self.json(response)["error"]["code"], "validation_error")
        self.assertIn("password", json.dumps(self.json(response)["details"]))
        self.assertFalse(User.objects.filter(username="dave").exists())
        invite.refresh_from_db()
        self.assertFalse(invite.is_used)

    def test_register_rate_limited(self):
        """注册按 IP 限流：超过阈值后连有效邀请码也不给用，且码不会被消耗。"""
        from .models import InviteCode
        from django.utils import timezone
        invite = InviteCode.objects.create(code="late", inviter=self.alice,
                                           expires_at=timezone.now() + timezone.timedelta(hours=1))
        bad = {"code": "nope", "username": "dave",
               "password1": "Zq!8mopw-v3", "password2": "Zq!8mopw-v3"}
        for _ in range(REGISTER_LIMIT):
            self.assertEqual(self.post_json("/api/v1/auth/register", bad).status_code, 404)
        blocked = self.post_json("/api/v1/auth/register", {**bad, "code": invite.code})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(self.json(blocked)["error"]["code"], "throttled")
        self.assertIn("Retry-After", blocked.headers)
        self.assertLessEqual(int(blocked.headers["Retry-After"]), REGISTER_WINDOW)
        invite.refresh_from_db()
        self.assertFalse(invite.is_used)

    def test_me_with_and_without_token(self):
        data = self.json(self.get_json("/api/v1/auth/me", self.alice_raw))
        self.assertEqual(data["username"], "alice")
        self.assertEqual(data["token"]["id"], self.alice_token.pk)
        self.assertFalse(data["is_staff"])
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_token_lifecycle(self):
        created = self.post_json("/api/v1/auth/tokens", {"name": "平板"}, self.alice_raw)
        self.assertEqual(created.status_code, 201, created.content)
        payload = self.json(created)
        plain = payload["token"]
        listing = self.json(self.get_json("/api/v1/auth/tokens", self.alice_raw))
        self.assertEqual(listing["count"], 2)
        self.assertNotIn("token", listing["results"][0])
        self.assertNotIn(plain, json.dumps(listing))

        # 新令牌可用
        self.assertEqual(self.get_json("/api/v1/posts?mine=true", plain).status_code, 200)
        revoked = self.delete_json(f"/api/v1/auth/tokens/{payload['id']}", self.alice_raw)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.get_json("/api/v1/posts?mine=true", plain).status_code, 401)

    def test_cannot_revoke_other_users_token(self):
        response = self.delete_json(f"/api/v1/auth/tokens/{self.alice_token.pk}", self.bob_raw)
        self.assertEqual(response.status_code, 404)
        self.alice_token.refresh_from_db()
        self.assertIsNone(self.alice_token.revoked_at)

    def test_token_cap(self):
        for i in range(MAX_ACTIVE_TOKENS - 1):
            self.post_json("/api/v1/auth/tokens", {"name": f"t{i}"}, self.alice_raw)
        overflow = self.post_json("/api/v1/auth/tokens", {"name": "超"}, self.alice_raw)
        self.assertEqual(overflow.status_code, 409)
        self.assertEqual(self.json(overflow)["error"]["code"], "conflict")

    def test_logout_revokes_current_token(self):
        response = self.post_json("/api/v1/auth/logout", {}, self.alice_raw)
        self.assertEqual(response.status_code, 200, response.content)
        self.alice_token.refresh_from_db()
        self.assertIsNotNone(self.alice_token.revoked_at)

    def test_profile_read_and_patch(self):
        data = self.json(self.get_json("/api/v1/account/profile", self.alice_raw))
        self.assertEqual(data["display_name"], "alice")
        self.assertEqual(data["email"], "")

        patched = self.patch_json("/api/v1/account/profile",
                                  {"display_name": "Alice", "github": "alice-dev",
                                   "bio": "写点东西"}, self.alice_raw)
        self.assertEqual(patched.status_code, 200, patched.content)
        result = self.json(patched)
        self.assertEqual(result["display_name"], "Alice")
        self.assertEqual(result["github"], "https://github.com/alice-dev")
        self.assertEqual(result["github_username"], "alice-dev")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.profile.bio, "写点东西")

    def test_profile_keeps_unsent_fields(self):
        self.patch_json("/api/v1/account/profile", {"bio": "只改简介"}, self.alice_raw)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.profile.display_name, "alice")
        self.assertEqual(self.alice.profile.bio, "只改简介")

    def test_profile_validation(self):
        response = self.patch_json("/api/v1/account/profile",
                                   {"display_name": "x" * 200}, self.alice_raw)
        self.assertEqual(response.status_code, 422)
        self.assertIn("display_name", self.json(response)["details"])

    def test_invites(self):
        created = self.post_json("/api/v1/account/invites", {}, self.alice_raw)
        self.assertEqual(created.status_code, 201, created.content)
        payload = self.json(created)
        self.assertIn("/accounts/register/", payload["url"])
        self.assertFalse(payload["is_used"])

        listing = self.json(self.get_json("/api/v1/account/invites", self.alice_raw))
        self.assertEqual(listing["count"], 1)

        second = self.post_json("/api/v1/account/invites", {}, self.alice_raw)
        self.assertEqual(second.status_code, 429, second.content)
        self.assertEqual(self.json(second)["error"]["code"], "invite_daily_limit")

    def test_staff_can_issue_many_invites(self):
        self.alice.is_staff = True
        self.alice.save(update_fields=["is_staff"])
        for _ in range(3):
            self.assertEqual(self.post_json("/api/v1/account/invites", {},
                                             self.alice_raw).status_code, 201)

    def test_session_cookie_csrf_is_enforced(self):
        """会话 Cookie 可用，但非幂等方法必须过 CSRF；Bearer 令牌与 CSRF 无关。"""
        strict = Client(enforce_csrf_checks=True)
        self.assertTrue(strict.login(username="alice", password="pw-alice"))
        self.assertEqual(strict.get("/api/v1/auth/me").status_code, 200)

        denied = strict.post("/api/v1/account/invites", data="{}",
                             content_type="application/json")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.json(denied)["error"]["code"], "csrf_failed")

        # 带上页面里那份真实令牌即放行 —— 站内页面将来改用这套接口的路径
        token = re.search(r'name="csrfmiddlewaretoken"[^>]*value="([^"]+)"',
                          strict.get("/accounts/login/").content.decode())
        self.assertIsNotNone(token)
        allowed = strict.post("/api/v1/account/invites", data="{}",
                              content_type="application/json",
                              headers={"X-CSRFToken": token.group(1)})
        self.assertEqual(allowed.status_code, 201, allowed.content)

        # 换了 Bearer 令牌的客户端不校验 CSRF：令牌不依赖 Cookie，天然免疫
        # 换成 Bearer 令牌的客户端不依赖 Cookie，与 CSRF 无关
        bearer = Client(enforce_csrf_checks=True).post(
            "/api/v1/account/invites", data="{}", content_type="application/json",
            **self.auth(self.bob_raw))
        self.assertEqual(bearer.status_code, 201, bearer.content)





class UploadApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self.media_root)
        self._override.enable()
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))
        self.addCleanup(self._override.disable)

        img = PILImage.new("RGB", (120, 60), color=(10, 200, 90))
        buf = BytesIO()
        img.save(buf, format="PNG")
        self.png = buf.getvalue()

    def upload(self, url, payload, token=None):
        headers = self.auth(token) if token else {}
        return self.client.post(url, data=payload, **headers)

    def test_upload_image(self):
        response = self.upload("/api/v1/uploads/image",
                               {"image": SimpleUploadedFile("pic.png", self.png,
                                                            content_type="image/png")},
                               self.alice_raw)
        self.assertEqual(response.status_code, 200, response.content)
        data = self.json(response)
        self.assertTrue(data["url"].endswith(".webp"))
        self.assertFalse(data["dedup"])
        self.assertEqual((data["width"], data["height"]), (120, 60))

    def test_upload_dedup_by_md5(self):
        payload = {"image": SimpleUploadedFile("pic.png", self.png, content_type="image/png")}
        self.upload("/api/v1/uploads/image", payload, self.alice_raw)
        again = self.upload("/api/v1/uploads/image",
                            {"image": SimpleUploadedFile("改名.png", self.png,
                                                         content_type="image/png")},
                            self.alice_raw)
        self.assertTrue(self.json(again)["dedup"])
        from .models import UploadedImage
        self.assertEqual(UploadedImage.objects.count(), 1)

    def test_upload_requires_auth(self):
        response = self.upload("/api/v1/uploads/image",
                               {"image": SimpleUploadedFile("pic.png", self.png,
                                                            content_type="image/png")})
        self.assertEqual(response.status_code, 401)

    def test_rejects_non_image(self):
        response = self.upload("/api/v1/uploads/image",
                               {"image": SimpleUploadedFile("a.txt", b"hello",
                                                            content_type="text/plain")},
                               self.alice_raw)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.json(response)["error"]["code"], "invalid_image")

    def test_rejects_too_large(self):
        response = self.upload("/api/v1/uploads/image",
                               {"image": SimpleUploadedFile("big.png", b"x" * (10 * 1024 * 1024 + 1),
                                                            content_type="image/png")},
                               self.alice_raw)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.json(response)["error"]["code"], "too_large")

    def test_list_own_images_only(self):
        self.upload("/api/v1/uploads/image",
                    {"image": SimpleUploadedFile("pic.png", self.png, content_type="image/png")},
                    self.alice_raw)
        self.assertEqual(self.json(self.get_json("/api/v1/uploads/images", self.alice_raw))["count"], 1)
        self.assertEqual(self.json(self.get_json("/api/v1/uploads/images", self.bob_raw))["count"], 0)

    def test_avatar_upload(self):
        response = self.upload("/api/v1/uploads/avatar",
                               {"image": SimpleUploadedFile("me.png", self.png,
                                                            content_type="image/png")},
                               self.alice_raw)
        self.assertEqual(response.status_code, 200, response.content)
        data = self.json(response)
        self.assertIn("avatars/alice.webp", data["url"])
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.profile.avatar.name.endswith("alice.webp"))


class PostViewCountApiTests(ApiTestCase):
    def url(self):
        return f"/api/v1/posts/{self.published.unique_id}/view"

    def test_counts_once_per_fingerprint(self):
        first = self.post_json(self.url(), {"fingerprint": "device-A"})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(self.json(first), {"counted": True, "views": 1})
        again = self.post_json(self.url(), {"fingerprint": "device-A"})
        self.assertEqual(self.json(again), {"counted": False, "views": 1})
        # 换指纹（同一 IP）仍在冷却期内
        other = self.post_json(self.url(), {"fingerprint": "device-B"})
        self.assertEqual(self.json(other)["counted"], False)

    def test_requires_fingerprint(self):
        response = self.post_json(self.url(), {"fingerprint": "  "})
        self.assertEqual(response.status_code, 422)

    def test_draft_needs_author(self):
        url = f"/api/v1/posts/{self.draft.unique_id}/view"
        self.assertEqual(self.post_json(url, {"fingerprint": "f"}).status_code, 404)
        self.assertEqual(self.post_json(url, {"fingerprint": "f"}, self.alice_raw).status_code, 200)
