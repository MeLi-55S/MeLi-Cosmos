"""
Seed management command for MeLi Cosmos v2.0.
Populates the database with sample data matching example.html.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from blog.models import Category, Tag, Post, Memo, UserProfile


class Command(BaseCommand):
    help = "Seed the database with initial sample data for MeLi Cosmos blog."

    def handle(self, *args, **options):
        self.stdout.write("🌌 Seeding MeLi Cosmos database...")

        # ── User ──────────────────────────────────────────────────
        user, created = User.objects.get_or_create(
            username="debris",
            defaults={
                "email": "debris@mycosmos.dev",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            user.set_password("admin")
            user.save()
        self.stdout.write(f"  ✅ User: {user.username}")

        # ── UserProfile ──────────────────────────────────────────
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "display_name": "Debris",
                "title": "全栈开发者 & 数字园丁",
                "bio": "Python / Django 开发者，明日方舟刀客塔，Obsidian 重度用户。在这个数字花园里记录技术思考与生活碎碎念。",
            },
        )
        self.stdout.write(f"  ✅ UserProfile: {profile.display_name}")

        # ── Categories ────────────────────────────────────────────
        cat_backend, _ = Category.objects.get_or_create(
            name="后端开发", defaults={"author": user}
        )
        cat_game, _ = Category.objects.get_or_create(
            name="游戏人生", defaults={"author": user}
        )
        Category.objects.get_or_create(name="系统运维", defaults={"author": user})
        Category.objects.get_or_create(name="数字花园", defaults={"author": user})
        self.stdout.write(f"  ✅ Categories created")

        # ── Tags ──────────────────────────────────────────────────
        tags_data = [
            "Django", "Python", "Obsidian", "Nginx", "DevOps",
            "Arknights", "Anime", "Linux", "Cloudflare",
        ]
        tag_objs = {}
        for name in tags_data:
            tag, _ = Tag.objects.get_or_create(name=name, defaults={"author": user})
            tag_objs[name] = tag
        self.stdout.write(f"  ✅ Tags created")

        # ── Posts ─────────────────────────────────────────────────
        post1_body = """有些生命的意义大概来自分享吧。在经历了一场热闹的戛然而止后，我决定用熟悉的 Python 和 Django，为自己筑造一座绝对自由、100%掌控的数字基地。把那些亮晶晶的想法和踩过的坑码成文字，挂在网络的角落里。

## 🛠️ 深夜的 ORM 抉择

在初始化 `Post` 模型时，我面临了一个经典的问题：自增 ID 还是 UUID？考虑到未来可能与 Obsidian 的 API 自动同步（那是另一个大大的"大干一场"项目），最终选择了 UUID。

这里是我的第一个核心 Model 设计片段：

```python
import uuid
from django.db import models

class Post(models.Model):
    # 极客同步专用键（强绑定本地 Obsidian 卡片）
    unique_id = models.UUIDField('外部同步ID', default=uuid.uuid4, editable=False)
    title = models.CharField('文章标题', max_length=200)
    body = models.TextField('文章正文')
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
```

## 🛰️ 防御与安全闭环

图片的异步处理也是一个大坑。我写了一个钩子，在图片上传时自动用 Pillow 压缩成 WebP 格式并异步上传至 Cloudflare R2，实现极致纯净的本土化。

这整个过程虽然枯燥，但在看到终端里跳出的那一连串的 `[OK]` 时，那股空虚感瞬间就被代码的香气冲散了。
"""

        post2_body = """罗德岛又进入了熟悉的草长莺飞期。看着干员专精的倒计时，我突然在想，能不能把基建的数据通过自定义脚本抓出来，直接呈现在我的 Django Dashboard 上。

## 🎮 自动化思路

明舟的基建系统其实是一个很好的数据流练习项目：

1. **数据采集层** — 通过抓包分析 HTTP 请求，提取制造站、贸易站、发电站的实时数据
2. **数据清洗层** — Python 脚本处理 JSON 响应，计算效率和收益曲线
3. **展示层** — Django REST API + 前端 Dashboard 面板

```python
def calculate_efficiency(manufacturing_data):
    # 计算基建制造站效率
    total_output = sum(station['output'] for station in manufacturing_data)
    max_theoretical = len(manufacturing_data) * 100
    return total_output / max_theoretical * 100
```

## 📝 干员数据映射

每个干员的技能加成都可以建模为 Python dataclass，这让我对类型系统有了更深的理解。罗德岛不只教会了我策略，还教会了我系统架构。

> "在长草期磨代码，在活动中检验真章" —— 某不知名刀客塔
"""

        post1 = Post.objects.filter(slug="django-v2-blog-from-midnight").first()
        if not post1:
            post1 = Post.objects.create(
                title="手敲 Django V2.0：从深夜的失落里捞出一个个人博客",
                slug="django-v2-blog-from-midnight",
                author=user,
                body=post1_body,
                category=cat_backend,
                status="published",
                created_time=timezone.now(),
            )
            post1.tags.add(tag_objs["Django"], tag_objs["Python"], tag_objs["Obsidian"])
            self.stdout.write(f"  ✅ Post 1: {post1.title}")

        post2 = Post.objects.filter(slug="arknights-auto-farming").first()
        if not post2:
            post2 = Post.objects.create(
                title="明日方舟长草期的碎碎念与自动化关线思考",
                slug="arknights-auto-farming",
                author=user,
                body=post2_body,
                category=cat_game,
                status="published",
                created_time=timezone.now() - timezone.timedelta(days=5),
            )
            post2.tags.add(tag_objs["Arknights"], tag_objs["Anime"])
            self.stdout.write(f"  ✅ Post 2: {post2.title}")

        # ── Memo ──────────────────────────────────────────────────
        memo_exists = Memo.objects.filter(is_public=True).exists()
        if not memo_exists:
            Memo.objects.create(
                content="今天把 Nginx 限流和防火墙策略盘明白了。等下把本地 Obsidian 的 API 接口对通，准备大干一场！",
                is_public=True,
                author=user,
            )
            self.stdout.write(f"  ✅ Memo created")

        self.stdout.write(self.style.SUCCESS("🌌 Database seeding complete! Run the server and explore."))
