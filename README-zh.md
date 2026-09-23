# MeLi Cosmos v3.0

> 极简主义多用户 Django 博客 — 纯 Markdown 写作，Tailwind CSS 渲染，零框架依赖。

## 快速开始

```bash
uv sync                                    # 安装依赖
uv run python manage.py migrate            # 执行数据库迁移
uv run python manage.py seed_data          # 填充示例数据（创建管理员 debris/admin）
uv run python manage.py runserver          # 启动开发服务器 → http://127.0.0.1:8000
```

管理后台：http://127.0.0.1:8000/admin/（用户名：`debris`，密码：`admin`）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Django 6.x（Python 3.14+） |
| 前端样式 | Tailwind CSS v4（CDN，无需构建） |
| Markdown | Python-Markdown + Pygments + nh3 净化器 |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） |
| WSGI | Gunicorn + Nginx + Cloudflare |
| 包管理 | uv |

## 项目结构

```
my_cosmos/              # Django 项目配置
├── settings.py
├── urls.py
└── wsgi.py / asgi.py

blog/                   # Django 应用
├── models.py           # Post, Memo, Category, Tag, Series, UserProfile, InviteCode, ViewLog, Like, Comment, BanAppeal
├── views.py            # 类视图 + AJAX 端点
├── urls.py
├── admin.py
├── forms.py
├── middleware.py        # BanCheckMiddleware
├── context_processors.py
├── feeds.py            # RSS/Atom 订阅
└── management/         # 自定义命令（seed_data, cleanup_view_logs 等）

blog/api/               # REST API（django-ninja，挂载于 /api/v1/）
├── app.py              # NinjaAPI 实例、异常处理器、router 挂载表
├── auth.py             # Bearer 认证三档依赖
├── common.py           # 分页 / 可见性 / 表单校验 / ref 解析
├── errors.py           # 统一错误体
├── schema.py           # 请求与响应模型
├── serializers.py      # 模型 → dict
└── routers/            # 按领域拆分的端点组（meta/posts/content/taxonomy/discovery/social/inbox/account/uploads）

dashboard/              # 管理员面板（仅 staff 可访问）
├── views.py
└── urls.py

templates/blog/         # Django 模板（Tailwind 样式）
├── base.html           # 根布局（暗色主题 + 外部链接安全弹窗）
├── index.html          # 三列网格 + 侧边栏
├── post_detail.html    # 单栏阅读视图
├── landing.html        # 着陆页
├── about.html          # 关于页面（服务器状态 + 赞赏二维码）
├── terms.html          # 用户协议
├── privacy.html        # 隐私政策
├── memo_detail.html    # 碎碎念详情（含点赞/评论）
├── ban_appeal.html     # 封禁申诉表单
└── includes/           # 导航栏、页脚、评论区、点赞 JS、用户布局
```

## 功能特性

- **多用户**平台，邀请码注册制
- **点赞 + 评论**系统 — AJAX 点赞，表单评论，"A、B等X人赞了"显示
- **纯 Markdown** 内容摄入，通过 UUID（`Post.unique_id`）与 Obsidian 同步，nh3 防 XSS
- **CC 许可协议**自选（7 种：CC BY/BY-SA/BY-NC/BY-NC-SA/BY-ND/BY-NC-ND/CC0），默认 CC BY-NC 4.0
- **暗色模式** canvas（`#050a14`），cyan-400 强调色，三态主题切换（亮色/暗色/跟随系统）
- **浏览器指纹 + IP 浏览计数**，可配置冷却期，防止无痕刷量
- **服务端 Markdown 渲染**，代码高亮，可折叠目录，nh3 净化
- **语义相关文章** — 加权评分算法（分类 + 标签重叠）
- **文章系列**支持，前后篇导航
- **碎碎念**（微博客），支持点赞和评论
- **草稿箱** — 每人独立的私密草稿列表
- **RSS 订阅**，支持按用户及全站
- **图片上传**，自动转 WebP、去重、剥离 EXIF，前端 10MB 即时校验
- **头像**裁剪上传，DiceBear 默认头像回退
- **响应式**三列布局，移动端汉堡菜单
- **Nginx/X-Forwarded-For** 适配生产环境
- **封禁系统** — 管理员封禁 + 邀请链递归，永久封禁标记，每次封禁限一次申诉
- **管理面板**（`/dashboard/`）— 站点统计、服务器状态、待处理申诉、用户邀请树
- **外部链接安全弹窗** — 自定义弹窗 + Google Safe Browsing 检测
- **用户协议** + **隐私政策**页面
- **赞赏**弹窗，微信/支付宝二维码
- **Select 下拉菜单**自定义 V 形图标 + 聚焦光环

## REST API（`/api/v1`）

基于 django-ninja，浏览器里可直接试用：**`/api/v1/docs`**（Swagger UI），机器可读规范：**`/api/v1/openapi.json`**，仓库快照：`docs/openapi.json`（改完端点跑 `manage.py export_openapi` 重生成，测试会检查快照与代码是否漂移）。

**认证**：三级，全部以 `Authorization: Bearer mlc_<40 hex>` 携带个人访问令牌（PAT）。令牌只在签发时明文出现一次，库里存 SHA-256，撤销用 `revoked_at` 软标记。

| 依赖 | 用于 | 行为 |
|---|---|---|
| 匿名 | `/health` `/meta` `/auth/login` `/auth/register` | 无需凭证 |
| `OptionalBearerAuth` | 所有读端点、`POST /comments` | 不带令牌=公开视角；带作者令牌=额外看到自己的草稿与私密内容；游客评论也走这档 |
| `BearerAuth` | 文章/碎碎念/分类法/点赞等写端点 | 必须有效令牌 |
| `SessionOrBearerAuth` | `/auth/me` `/auth/tokens` `/account/*` `/inbox` `/uploads` | 令牌或浏览器会话皆可；走会话时补一次 CSRF 校验 |

**端点分组**（49 个操作）：站点 2 · 文章 6 · 碎碎念 4 · 分类法 9 · 发现 5 · 评论与点赞 7 · 收件箱 3 · 认证与令牌 6 · 账号 4 · 上传 3。

**统一错误体**：`{"error": {"code": "not_found", "message": "文章不存在"}}`，校验失败额外带 `details: {字段: [消息]}`，限流带 `Retry-After` 头。列表统一 `{count, page, page_size, total_pages, results}`，`page_size` 上限 100。

**与站内同一套规则**：markdown 渲染、点赞文案、评论审核与限流、游客首评过审、蜜罐、图片转 WebP 与 MD5 去重、邀请码每日限额、浏览量冷却，全部复用 `blog/views.py` 里的实现，移动端不会比浏览器多出任何权限。

```bash
# 登录换令牌（注册需邀请码），之后所有写请求带 Authorization 头
curl -s -X POST https://blog.example.com/api/v1/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"me","password":"…","device_name":"pixel"}'
```

## 配置项

| 环境变量 / 设置 | 默认值 | 说明 |
|---|---|---|
| `DJANGO_DEBUG` | `True` | 调试模式 |
| `DJANGO_SECRET_KEY` | （内置开发密钥） | 密钥 |
| `DJANGO_ALLOWED_HOSTS` | `*` | 允许的主机（逗号分隔） |
| `DB_ENGINE` | `django.db.backends.sqlite3` | 数据库引擎 |
| `DB_NAME` | `db.sqlite3` | 数据库名称 / 路径 |
| `VIEW_LOG_COOLDOWN_HOURS` | `1` | 同一指纹+IP 重新计数的冷却小时数 |
| `VIEW_LOG_RETENTION_DAYS` | `90` | 浏览日志保留天数 |
| `SERVER_DISPLAY_NAME` | （hostname） | 关于页面的服务器标识 |
| `INVITE_CODE_EXPIRE_HOURS` | `24` | 邀请码有效期 |
| `INVITE_DAILY_LIMIT` | `1` | 每人每天最多未使用邀请码数 |
| `GOOGLE_SAFE_BROWSING_KEY` | （空） | Google Safe Browsing API 密钥，用于外部链接检测 |

## 管理命令

```bash
uv run python manage.py seed_data              # 填充示例数据
uv run python manage.py cleanup_view_logs       # 清理过期浏览日志
uv run python manage.py test                    # 运行测试
uv run python manage.py export_openapi         # 重生成 docs/openapi.json
```

## 许可

代码：MIT。用户内容：作者自选 CC 许可协议（默认 CC BY-NC 4.0）。
