"""REST API（``/api/v1``）测试。

覆盖：令牌签发与鉴权、错误响应格式、文章/碎碎念的读写与可见性规则。
运行：``uv run python manage.py test tests_api``（或整包 ``manage.py test``）。
"""

from urllib.parse import quote

from django.test import Client, TestCase
from django.contrib.auth.models import User

from . import models as blog_models
from .models import ApiToken, Category, Memo, Post, Tag

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
