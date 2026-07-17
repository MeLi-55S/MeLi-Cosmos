# MeLi Cosmos v2.0 多用户重构需求规格说明书

> 文档版本：v1.0  
> 目标版本：v3.0（多用户里程碑）  
> 适用范围：开发团队技术方案设计与实施

---

## 目录

1. [现状概述](#1-现状概述)
2. [多用户架构设计](#2-多用户架构设计)
3. [核心业务场景的多用户适配逻辑与数据隔离方案](#3-核心业务场景的多用户适配逻辑与数据隔离方案)
4. [现存单体/单用户逻辑的重构范围与接口改动清单](#4-现存单体单用户逻辑的重构范围与接口改动清单)
5. [非功能性需求](#5-非功能性需求)
6. [实施路线建议](#6-实施路线建议)

---

## 1. 现状概述

### 1.1 项目定位

MeLi Cosmos v2.0 是一个 Django 6.x 单体个人博客系统，采用 Zettelkasten 哲学，核心理念为"一人一号一站"。当前代码虽然使用了 Django 内置 `auth.User` 模型并为 `Post` 设置了 `author` 外键，但在架构层面是准单用户设计：

- 唯一用户通过 `seed_data` 管理命令创建（用户名 `debris`，超级管理员）
- 分类（Category）、标签（Tag）、系列（Series）、碎碎念（Memo）均无用户归属字段
- 个人资料卡片（Profile Card）在 `context_processors.py` 中硬编码
- RSS Feed、Sitemap 均面向全站而非按用户拆分
- 前台页面无任何"当前作者空间"概念

### 1.2 当前数据模型关系图

```
User (django.contrib.auth.models.User)
 ├── Post (author FK → User)
 │     ├── Category (FK → Category)    ← 全局共享，无 owner
 │     ├── Tag (M2M → Tag)             ← 全局共享，无 owner
 │     └── Series (FK → Series)        ← 全局共享，无 owner
 ├── Memo                              ← 无 author 字段
 └── InviteCode                        ← 不存在（无邀请机制）
       ├── inviter FK → User
       └── invitee FK → User
```

### 1.3 多用户重构目标

将 MeLi Cosmos 从"个人单博客"升级为"多用户独立博客平台"，其中：

- 每个注册用户拥有一套独立的博客空间（独立文章、分类、标签、系列、碎碎念）
- 首页为聚合展示（所有用户的公开文章按时间线排列）
- 每个用户拥有唯一的个人主页（`/@username/`），展示该用户的所有公开内容
- 保留 Zettelkasten 哲学与 Obsidian UUID 同步机制
- 前端保持 Tailwind CSS + 零构建步骤的极简约束

---

## 2. 多用户架构设计

### 2.1 用户角色划分

| 角色 | 权限范围 | 说明 |
|------|---------|------|
| **匿名访客** | 浏览所有已发布文章、碎碎念、归档、系列 | 无需登录，可搜索 |
| **注册作者** | 管理自己的文章（CRUD + 发布/草稿/私密）、分类、标签、系列、碎碎念；编辑个人资料；生成邀请码（每日限 1 个） | 核心用户角色 |
| **管理员** | Django Admin 全权限（管理用户、所有内容） | 后台运维角色 |
| **超级管理员** | 同管理员，附加系统级配置 | 保留 Django superuser |

> **设计决策**：不引入中间角色（如"编辑""审稿人"），保持角色模型简单，降低初期实现复杂度。如需扩展可后续引入 Django Guardian 或自定义 Permission。

### 2.2 权限模型

采用 **基于对象所有权的自主访问控制（DAC）** 模型：

```
规则：
1. 匿名用户：仅可读取 status='published' 的内容；可凭有效邀请码注册
2. 认证用户（作者本人）：
   - 可读取自己的所有状态内容（published / draft / private）
   - 可对自己的内容执行创建、编辑、发布、删除操作
   - 可生成邀请码（非管理员每日限 1 个，管理员不限）
3. 认证用户（非作者）：
   - 可读取其他用户的 published 内容
   - 不可访问其他用户的 draft / private 内容
   - 不可操作其他用户的内容
4. 管理员：通过 Django Admin 管理一切；邀请码生成无频率限制
```

**权限矩阵**：

| 操作 | 匿名 | 作者(自己) | 作者(他人) | 管理员 |
|------|------|-----------|-----------|--------|
| 查看 published 文章 | ✅ | ✅ | ✅ | ✅ |
| 查看 draft 文章 | ❌ | ✅ | ❌ | ✅ |
| 查看 private 文章 | ❌ | ✅ | ❌ | ✅ |
| 创建文章 | ❌ | ✅ | — | ✅ |
| 编辑文章 | ❌ | ✅ | ❌ | ✅ |
| 删除文章 | ❌ | ✅ | ❌ | ✅ |
| 创建分类/标签/系列 | ❌ | ✅ | — | ✅ |
| 查看他人分类/标签/系列（published 范围内） | ✅ | ✅ | ✅ | ✅ |
| 编辑/删除他人的分类/标签/系列 | ❌ | ❌ | ❌ | ✅ |
| 生成邀请码 | ❌ | ✅（限 1/天） | ✅（限 1/天） | ✅（不限） |
| 查看自己的邀请码及使用状态 | ❌ | ✅ | — | ✅ |

### 2.3 认证与授权机制

#### 2.3.1 认证方案

**保持 Django 内置 Session 认证**，在现有基础上扩展：

| 机制 | 现状 | 变更 |
|------|------|------|
| 认证后端 | `django.contrib.auth.backends.ModelBackend` | 保持不变 |
| 登录入口 | `/accounts/login/`（Django LoginView） | **变更**：增加邀请码领取入口 `/accounts/invite/` |
| 注册流程 | 无（仅 admin 创建） | **新增**：已有用户生成一次性邀请码 → 被邀请人凭邀请码注册 → 创建 User + UserProfile + 记录邀请关系 |
| 会话管理 | DB-backed session, 2 周有效期 | 保持不变 |
| CSRF 保护 | 标准 Django CSRF 中间件 | 保持不变 |

#### 2.3.2 授权实现

**方案：CBV Mixin + 函数级权限检查**

```python
# 建议新建 blog/mixins.py 统一管理权限逻辑

class AuthorRequiredMixin(UserPassesTestMixin):
    """检查当前用户是否为目标对象的作者"""
    def test_func(self):
        obj = self.get_object()
        return obj.author == self.request.user

class AuthorOrPublishedMixin:
    """对象级可见性：作者本人可看全部，他人仅看 published"""
    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated:
            return qs.filter(
                Q(status='published') | Q(author=user)
            )
        return qs.filter(status='published')
```

> 现有 `PostUpdateView`、`PostDeleteView`、`PostPublishView` 中已使用 `UserPassesTestMixin` 进行作者检查，重构时统一提升为 `AuthorRequiredMixin`。

#### 2.3.3 用户注册与邀请策略

采用 **一次性邀请码注册制**，杜绝开放注册，保证用户质量与可控增长。

##### 邀请码模型（`InviteCode`）

```
InviteCode
├── code          CharField(32)   邀请码（随机生成，如 urandom(16).hex()）
├── inviter       FK → User       邀请人（谁创建了此邀请码）
├── invitee       FK → User       被邀请人（谁使用此码注册，可为空，注册时回填）
├── is_used       BooleanField    是否已被使用（默认 False）
├── created_at    DateTimeField   创建时间
├── expires_at    DateTimeField   过期时间（创建后 24 小时）
├── used_at       DateTimeField   使用时间（可为空）
```

##### 邀请码生命周期

```
创建（inviter 点击生成）
  ├── 频率检查：非管理员每日限 1 个，管理员不限
  ├── 生成随机 32 位 hex 码
  ├── 设置 expires_at = now + 24h
  └── 展示给 inviter（可复制分享）

注册（invitee 提交注册表单）
  ├── 校验邀请码是否存在
  ├── 校验 is_used == False
  ├── 校验 expires_at > now（未过期）
  ├── 创建 User → 创建 UserProfile
  ├── 回填 InviteCode.invitee、is_used=True、used_at=now
  └── 自动登录 → 跳转首页

过期清理
  └── 定时任务（Django management command + cron）/ 或查询时过滤
```

##### 频率限制规则

| 角色 | 限制 | 实现方式 |
|------|------|---------|
| **管理员（is_staff）** | 无限制 | 跳过频率检查 |
| **普通用户** | 每自然日最多创建 1 个未使用邀请码 | `InviteCode.objects.filter(inviter=user, created_at__date=date.today()).count() < 1` |

> **注意**：频率限制基于"当天已创建的未使用邀请码数量"，而非累计总数。如果用户当天创建了一个邀请码但 24 小时内未被使用（自然过期），该用户次日即可重新创建。

##### 可追溯性

- `InviteCode.inviter` 记录邀请人，`InviteCode.invitee` 记录被邀请人
- 管理员可在 Django Admin 中查看完整的邀请关系链
- 未来可扩展邀请排行榜、邀请树等社交功能

---

## 3. 核心业务场景的多用户适配逻辑与数据隔离方案

### 3.1 数据隔离策略：行级归属（Row-Level Ownership）

**核心原则**：所有用户产生的内容（Content）必须在数据库行级别标记所属用户。

| 模型 | 隔离字段 | 方法 |
|------|---------|------|
| `Post` | `author` FK → User | **已存在，保持不变** |
| `Category` | `author` FK → User | **新增** |
| `Tag` | `author` FK → User | **新增** |
| `Series` | `author` FK → User | **新增** |
| `Memo` | `author` FK → User | **新增** |
| `InviteCode`（新增） | `inviter` FK → User, `invitee` FK → User | **新模型** |
| `UserProfile`（新增） | `user` OneToOne → User | **新模型** |

> **设计决策**：Category、Tag、Series 设为**按用户隔离**而非全局共享。理由：
> 1. 每个作者对分类体系有不同的认知模型，不应强制统一
> 2. 避免跨用户的命名冲突（如两个用户都创建 "Python" 分类）
> 3. 符合"独立博客空间"的产品定位
> 4. 如需跨用户标签发现，可在未来通过标签名相似度推荐功能实现

**唯一性约束调整**：

| 模型 | 当前约束 | 重构后约束 |
|------|---------|-----------|
| `Category.slug` | `unique=True` | `unique_together = ('slug', 'author')` |
| `Tag.slug` | `unique=True` | `unique_together = ('slug', 'author')` |
| `Series.slug` | `unique=True` | `unique_together = ('slug', 'author')` |
| `Post.slug` | `unique=True` | `unique_together = ('slug', 'author')` |

> **注意**：当前 `Post.slug` 已经是 unique，多用户后同一 slug（如 `hello-world`）可能被不同作者使用。slug 加 author 联合唯一后，URL 策略需调整为 `/@username/post/slug/`。

### 3.2 核心业务场景逐项分析

#### 场景 1：首页（聚合流）

**现状**：`IndexView` 展示所有 published 文章（+ 当前用户自己的草稿和私密），附带当前用户的仪表盘统计。

**多用户适配**：
```
变更：
1. 查询范围：所有用户的 published 文章（全局时间线）
2. 当前用户的 draft/private 文章也在流中出现（仅作者本人可见）
3. Dashboard 统计仅展示当前用户的数据（已实现）
4. 新增"作者筛选"链接：每篇文章卡片显示作者名，点击可进入该作者的个人主页
5. 右侧 Profile Card：未登录时展示平台简介；登录后展示当前用户的个人卡片
```

#### 场景 2：个人主页（用户空间）

**新增路由**：`/@<username>/`

```
展示内容：
- 该作者的公开文章列表（分页）
- 该作者的公开碎碎念
- 该作者的个人资料卡片（UserProfile）
- 该作者的系列列表 & 分类列表
- [仅作者本人] Dashboard 统计（文章数、浏览数）
- [仅作者本人] 草稿 / 私密文章入口
```

**新增 View**：`UserSpaceView(ListView)` — 按 author 过滤

#### 场景 3：文章详情页

**URL 调整**：`/@<username>/post/<slug>/`（替代当前 `/post/<slug>/`）

**多用户适配**：
```
变更：
1. get_object() 增加 author__username 定位 → 先查用户，再查文章
2. 权限检查保持：published 公开；draft/private 仅作者本人可看
3. 系列导航（prev/next）限定当前作者的系列
4. 相关文章推荐限定为同一作者（或扩展为全局）
5. 视图计数保持 session-based，不跨用户
6. 文章底部操作按钮仅该作者可见（已实现）
```

#### 场景 4：文章 CRUD

**现状**：
- 创建：`/post/create/` → `PostCreateView`，自动设置 author
- 编辑：`/post/<unique_id>/edit/` → `PostUpdateView`，检查作者身份
- 删除：`/post/<unique_id>/delete/` → `PostDeleteView`，检查作者身份
- 发布：`/post/<unique_id>/publish/` → `PostPublishView`，检查作者身份

**多用户适配**：
```
变更：
1. 创建页 URL 不变（/post/create/），author 自动设为 request.user（已实现）
2. 创建页的表单中 Category / Series 下拉选项限定为当前用户的
3. 创建页的标签建议（datalist）限定为当前用户的已有标签
4. 编辑/删除/发布 URL 保持不变，权限检查逻辑提升为统一 Mixin
5. 成功跳转 URL 更新为 /@username/post/slug/ 格式
```

#### 场景 5：分类/标签/系列列表与筛选

**现状**：
- `PostByTagView`：`/tag/<slug>/` → 按标签过滤所有公开文章
- `PostByCategoryView`：`/category/<slug>/` → 按分类过滤所有公开文章
- `PostBySeriesView`：`/series/<slug>/` → 按系列过滤所有公开文章
- `SeriesListView`：`/series/` → 列出所有系列

**多用户适配**：
```
变更（方案 A — 全局视图 + 用户空间嵌套）：
1. PostByTagView URL → /@<username>/tag/<slug>/   （仅该作者的文章）
2. PostByCategoryView URL → /@<username>/category/<slug>/
3. PostBySeriesView URL → /@<username>/series/<slug>/
4. SeriesListView URL → /@<username>/series/
5. 以上 View 的 get_queryset() 增加 author 过滤
6. 全局标签云、分类概览可作为发现页独立实现

变更（方案 B — 全局视图保持不变）：
1. 所有公开文章的标签/分类/系列聚合展示
2. 个人管理页中单独展示用户自己的标签/分类/系列

推荐：方案 A（用户空间内独立），符合独立博客产品定位。
```

#### 场景 6：搜索

**现状**：`SearchView` 搜索所有 published 文章的 title 和 body。

**多用户适配**：
```
变更：
1. 全局搜索：保持现状，搜索所有 published 文章
2. [可选] 用户空间内搜索：/@username/search/?q=xxx → 限定作者
3. 搜索结果中每篇文章显示作者名，可点击进入作者主页
```

#### 场景 7：归档页

**现状**：`ArchivesView` 按年-月分组所有 published 文章。

**多用户适配**：
```
变更：
1. URL → /@<username>/archives/
2. queryset 限定当前用户（author 过滤）
3. 全局归档页（/archives/）作为全站聚合保留
```

#### 场景 8：碎碎念（Memo）

**现状**：
- `MemoListView`：`/memos/` → 匿名仅看公开，认证用户看全部
- `MemoCreateView`：`/memo/create/` → 登录即可创建，无作者绑定
- `context_processors.profile`：取最新一条公开 Memo 用于导航栏状态指示

**多用户适配**：
```
变更：
1. Memo 模型新增 author FK（必填，迁移时需处理历史数据）
2. MemoListView URL → /@<username>/memos/（仅该作者的 memo）
3. 全局 memo 流：/memos/ 展示所有用户公开 memo（全局 timeline）
4. MemoCreateView：自动绑定 request.user 为 author
5. 导航栏"状态"指示：改为显示当前用户的 latest_memo（仅登录用户）
6. 侧边栏快速发布 Memo：保留，绑定当前用户
```

#### 场景 9：RSS Feed

**现状**：单个 Feed `/feed/` 输出最新 20 篇 published 文章。

**多用户适配**：
```
变更：
1. 全局 RSS：/feed/ → 保持，输出所有用户的最新公开文章
2. 用户级 RSS：/@<username>/feed/ → 新增，输出该作者的最新公开文章
3. Feed 标题和描述需动态化
```

#### 场景 10：Sitemap

**现状**：单一 `PostSitemap` + `StaticViewSitemap`。

**多用户适配**：
```
变更：
1. PostSitemap.items() 保持查询所有 published 文章（SEO 角度无需改动）
2. 新增用户主页、用户归档页等 URL 到 StaticViewSitemap
```

#### 场景 11：个人资料

**现状**：`context_processors.py` 中硬编码 `PROFILE_DATA` 字典，包含 name、title、avatar_seed、bio、stats。

**多用户适配**：
```
变更：
1. 新增 UserProfile 模型（OneToOne → User）
   fields: display_name, bio, avatar_url, title, website, github, mastodon
2. 重写 context_processor.profile()
   - 登录用户 → 返回当前用户的 UserProfile
   - 匿名用户 → 返回平台默认信息（站点简介）
3. 用户个人主页 → 展示该作者的 UserProfile
4. 统计信息改为动态查询（Post 数量等）
5. footer 版权信息改为动态
```

### 3.3 URL 路由重构方案

```
当前 URL                          →  重构后 URL
─────────────────────────────────────────────────────
/                                 →  /                          （全局时间线）
/post/<slug>/                     →  /@<username>/post/<slug>/ （文章详情）
/post/create/                     →  /post/create/              （创建文章，不变）
/post/<uuid>/edit/                →  /post/<uuid>/edit/         （编辑文章，不变）
/post/<uuid>/delete/              →  /post/<uuid>/delete/       （删除文章，不变）
/post/<uuid>/publish/             →  /post/<uuid>/publish/      （发布文章，不变）
/tag/<slug>/                      →  /@<username>/tag/<slug>/  （标签筛选）
/category/<slug>/                 →  /@<username>/category/<slug>/
/series/<slug>/                   →  /@<username>/series/<slug>/
/series/                          →  /@<username>/series/       （系列列表）
/series/create/                   →  /series/create/            （不变，属于当前用户）
/archives/                        →  /@<username>/archives/     （用户归档）
/memos/                           →  /memos/                    （全局 memo 流）
                                    /@<username>/memos/          （用户 memo 流，新增）
/memo/create/                     →  /memo/create/              （不变）
/search/                          →  /search/                   （全局搜索）
                                    /@<username>/search/         （用户空间内搜索，可选）
/feed/                            →  /feed/                     （全局 RSS）
                                    /@<username>/feed/           （用户 RSS，新增）
/accounts/login/                  →  /accounts/login/           （不变）
/accounts/invite/                 →  /accounts/invite/          （新增：邀请码管理页）
/accounts/register/<code>/        →  /accounts/register/<code>/ （新增：凭邀请码注册）
/@<username>/                     →  /@<username>/              （用户主页，新增）
/about/                           →  /about/                    （平台关于页）
/admin/                           →  /admin/                    （不变）
```

---

## 4. 现存单体/单用户逻辑的重构范围与接口改动清单

### 4.1 模型层改动

#### 4.1.1 `blog/models.py`

| 改动项 | 类型 | 说明 |
|--------|------|------|
| **新增 `UserProfile` 模型** | 新增 | OneToOne → User；字段：display_name, bio, avatar_url, title, website, github, mastodon |
| **新增 `InviteCode` 模型** | 新增 | 字段：code(CharField, unique), inviter(FK→User), invitee(FK→User, null), is_used(Boolean), created_at, expires_at, used_at |
| `Category` 新增 `author` 字段 | 修改 | FK → User, null=False, default 需提供数据迁移脚本 |
| `Category.Meta` 新增联合唯一约束 | 修改 | `unique_together = [('slug', 'author')]`；移除单字段 `unique=True` |
| `Tag` 新增 `author` 字段 | 修改 | 同上 |
| `Tag.Meta` 新增联合唯一约束 | 修改 | 同上 |
| `Series` 新增 `author` 字段 | 修改 | 同上 |
| `Series.Meta` 新增联合唯一约束 | 修改 | 同上 |
| `Post.Meta` slug 联合唯一 | 修改 | `unique_together = [('slug', 'author')]`；移除 `slug` 单字段 `unique=True` |
| `Memo` 新增 `author` 字段 | 修改 | FK → User, null=False, default 需数据迁移 |

**数据迁移注意事项**：
- 所有新增 author 字段的模型，其历史数据的 author 必须指向现有用户（`debris`）
- 需编写 data migration（非 schema migration），确保迁移可逆

#### 4.1.2 `blog/forms.py`

| 改动项 | 类型 | 说明 |
|--------|------|------|
| `PostForm`：category queryset 过滤 | 修改 | `self.fields['category'].queryset = Category.objects.filter(author=user)` |
| `PostForm`：series queryset 过滤 | 修改 | `self.fields['series'].queryset = Series.objects.filter(author=user)` |
| `PostForm`：tag_names datalist 过滤 | 修改 | `Tag.objects.filter(author=user).values_list('name', flat=True)` |
| **新增 `UserProfileForm`** | 新增 | 用于用户编辑个人资料 |

### 4.2 视图层改动

#### 4.2.1 需新增的 View

| View 名称 | 类型 | URL | 说明 |
|-----------|------|-----|------|
| `UserSpaceView` | ListView | `/@<username>/` | 用户主页，展示该用户的公开文章、memo、资料 |
| `InviteCodeGenerateView` | View | `/accounts/invite/` | 邀请码管理页（生成 + 列表 + 状态） |
| `InviteRegisterView` | CreateView | `/accounts/register/<code>/` | 凭邀请码注册页，code 从 URL 路径传入 |
| `UserProfileUpdateView` | UpdateView | `/accounts/profile/edit/` | 个人资料编辑页 |
| `UserFeedView` | Feed | `/@<username>/feed/` | 用户级 RSS Feed |
| `UserArchivesView` | ListView | `/@<username>/archives/` | 用户归档页 |
| `UserMemoListView` | ListView | `/@<username>/memos/` | 用户碎碎念列表 |

#### 4.2.2 需修改的 View

| View | 改动点 |
|------|--------|
| **`IndexView`** | 保持不变（已是多用户兼容） |
| **`PostDetailView`** | URL pattern 增加 username 参数；`get_object()` 增加 user 过滤；slug 查找范围限作者 |
| **`PostCreateView`** | 表单 queryset 限定当前用户的 Category/Series；标签建议限定当前用户的 Tag |
| **`PostUpdateView`** | 权限检查改为统一 `AuthorRequiredMixin`；成功 URL 改为 `/@username/post/slug/` |
| **`PostDeleteView`** | 同上 |
| **`PostPublishView`** | 同上 |
| **`PostByTagView`** | URL 增加 username；queryset 增加 author 过滤 |
| **`PostByCategoryView`** | 同上 |
| **`PostBySeriesView`** | 同上 |
| **`SeriesListView`** | URL 增加 username；queryset 增加 author 过滤 |
| **`SeriesCreateView`** | 自动设置 author 为 request.user |
| **`ArchivesView`** | URL 增加 username（用户归档）；queryset 增加 author 过滤 |
| **`MemoListView`** | 全局 memo 流保持不变；新增用户级 `UserMemoListView` |
| **`MemoCreateView`** | `form_valid()` 自动设置 `form.instance.author = self.request.user` |
| **`SearchView`** | 全局搜索保持不变；搜索结果中显示作者名 |

#### 4.2.3 需删除的 View

| View | 原因 |
|------|------|
| 无 | 所有现有 View 均需保留并修改 |

### 4.3 URL 路由层改动（`blog/urls.py`）

```python
# 新增路由（约 10 条）
path("@<str:username>/", views.UserSpaceView.as_view(), name="user_space"),
path("@<str:username>/post/<uslug:slug>/", views.PostDetailView.as_view(), name="post_detail"),
path("@<str:username>/tag/<uslug:slug>/", views.PostByTagView.as_view(), name="post_by_tag"),
path("@<str:username>/category/<uslug:slug>/", views.PostByCategoryView.as_view(), name="post_by_category"),
path("@<str:username>/series/<uslug:slug>/", views.PostBySeriesView.as_view(), name="post_by_series"),
path("@<str:username>/series/", views.SeriesListView.as_view(), name="series_list"),
path("@<str:username>/archives/", views.ArchivesView.as_view(), name="user_archives"),
path("@<str:username>/memos/", views.UserMemoListView.as_view(), name="user_memo_list"),
path("@<str:username>/feed/", views.UserFeedView(), name="user_rss_feed"),
path("accounts/invite/", views.InviteCodeGenerateView.as_view(), name="invite"),
path("accounts/register/<str:code>/", views.InviteRegisterView.as_view(), name="register"),
path("accounts/profile/edit/", views.UserProfileUpdateView.as_view(), name="profile_edit"),

# 删除/修改路由（约 7 条）
# 旧路由在兼容期保留带重定向
```

### 4.4 模板层改动

| 模板文件 | 改动说明 |
|---------|---------|
| **`base.html`** | 全局 Meta 信息调整为平台描述；OG 标签支持动态 |
| **`nav.html`** | 导航链接增加用户空间入口；登录后显示邀请入口；搜索范围增加"当前用户"选项 |
| **`profile_card.html`** | 完全重构：根据上下文显示当前用户或目标用户的 Profile |
| **`dashboard.html`** | 增加链接指向 `/@username/` 用户空间管理 |
| **`index.html`** | 文章卡片增加作者显示和作者主页链接 |
| **`post_detail.html`** | 所有链接更新为 `@username` 格式；面包屑导航增加作者 |
| **`post_form.html`** | 保持，但 Category/Series 下拉选项已由后端过滤 |
| **`memo_list.html`** | 全局 memo 流增加作者名显示 |
| **`archives.html`** | 增加用户名上下文，调整为用户归档 |
| **`series_list.html`** | 增加用户名上下文，post_set 计数已自动按作者过滤 |
| **`footer.html`** | 年份动态化；作者名动态化 |
| **新增 `user_space.html`** | 用户主页模板 |
| **新增 `invite.html`** | 邀请码管理页（生成按钮 + 已生成邀请码列表及状态） |
| **新增 `register.html`** | 注册页模板（需携带邀请码，展示邀请人信息） |
| **新增 `profile_edit.html`** | 个人资料编辑模板 |
| **新增 `includes/user_profile_card.html`** | 用户资料卡片组件 |

### 4.5 辅助模块改动

| 模块 | 改动说明 |
|------|---------|
| **`context_processors.py`** | 重构 `profile()`：登录用户返回其 UserProfile，匿名返回平台简介；latest_memo 按当前用户过滤 |
| **`feeds.py`** | `LatestPostsFeed` 保持不变（全局）；新增 `UserPostsFeed`（用户级） |
| **`sitemaps.py`** | `PostSitemap` 保持不变；`StaticViewSitemap` 增加用户空间相关 URL 的生成逻辑 |
| **`admin.py`** | 所有 ModelAdmin 增加 `author` 字段显示和 `list_filter`；新增 `UserProfileAdmin`、`InviteCodeAdmin` |
| **`management/commands/seed_data.py`** | 适配新模型结构：创建 UserProfile；为历史数据填充 author；为 debris 用户预生成一个邀请码供测试 |
| **新增 `management/commands/cleanup_expired_invites.py`** | 定时清理过期邀请码（配合 cron 使用） |
| **新增 `blog/mixins.py`** | 统一 `AuthorRequiredMixin`、`AuthorOrPublishedMixin` |

### 4.6 配置层改动（`my_cosmos/settings.py`）

| 配置项 | 改动 |
|--------|------|
| 新增 `INVITE_CODE_EXPIRE_HOURS` | 邀请码有效期（小时），默认 `24` |
| 新增 `INVITE_DAILY_LIMIT` | 非管理员每日最大邀请数，默认 `1` |
| 新增 `AUTH_PROFILE_MODULE` | 指向 `blog.UserProfile`（Django 6 可能不再需要，通过 OneToOne 访问） |

---

## 5. 非功能性需求

### 5.1 性能约束

| 维度 | 要求 | 实现策略 |
|------|------|---------|
| **首页查询** | ≤ 3 条 SQL（含分页） | 保持 `select_related('category', 'author')` + `prefetch_related('tags')` |
| **个人主页** | ≤ 4 条 SQL | author 过滤 + select_related，避免 N+1 |
| **文章详情** | ≤ 5 条 SQL | 当前已较优；新增 UserProfile 联查 |
| **分页大小** | 保持 10 条/页 | 不变 |
| **数据库索引** | `(author, slug)` 联合索引 | 所有按用户 + slug 查询的场景 |
| **数据库索引** | `(author, status, created_time)` 联合索引 | 用户文章列表查询 |

**新增索引建议**：

```python
class Post(models.Model):
    class Meta:
        indexes = [
            models.Index(fields=['author', 'slug']),
            models.Index(fields=['author', 'status', '-created_time']),
            models.Index(fields=['status', '-created_time']),  # 全局时间线
        ]

class Category(models.Model):
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['author', 'slug'], name='unique_category_per_author')
        ]

# Tag, Series 同理
```

### 5.2 并发控制

| 场景 | 策略 |
|------|------|
| **文章编辑冲突** | 乐观锁：编辑页记录 `modified_time`，提交时校验（`update` with `WHERE modified_time = old_value`）。如发生冲突则提示用户刷新 |
| **Slug 生成冲突** | slugify 后追加随机后缀（如 `hello-world-a1b2`） |
| **UUID 唯一性** | 依赖数据库 unique 约束 + `uuid.uuid4()` 理论无冲突 |
| **视图计数并发** | 保持现有 `F('views') + 1` 方案，数据库原子操作保证正确性 |

### 5.3 安全约束

| 维度 | 要求 |
|------|------|
| **水平越权防护** | 所有 CRUD 操作必须校验 `obj.author == request.user` |
| **垂直越权防护** | 非管理员不可通过 URL 参数访问其他用户的 draft/private 内容 |
| **XSS 防护** | Markdown 渲染输出使用 `|safe`，但需确保 python-markdown 的输出安全（其默认转义 HTML） |
| **CSRF 防护** | 所有 POST/PUT/DELETE 表单包含 `{% csrf_token %}` |
| **注册安全** | 仅邀请码注册，无公开入口；用户名唯一性校验；密码强度通过 Django 内置 validators；邀请码为 32 位随机 hex，不可枚举 |
| **邀请码安全** | 一次性使用（is_used 状态位 + 数据库约束）；24 小时自动过期；非管理员每日限 1 个 |
| **频率限制** | 文章创建：每用户每分钟最多 5 篇；登录：每 IP 每分钟最多 10 次；邀请码生成：非管理员每日 1 个（业务层校验 + ip 粒度兜底限流）；邀请码核验：每 IP 每小时最多 5 次（建议使用 django-ratelimit 或 Nginx limit_req） |

### 5.4 兼容性约束

| 维度 | 要求 |
|------|------|
| **现有内容迁移** | 所有现有数据（Posts、Categories、Tags、Series、Memos）必须完整迁移，author 指向原用户 `debris` |
| **旧 URL 兼容** | 旧格式 `/post/<slug>/` 在过渡期保留并 301 重定向到 `/<owner_username>/post/<slug>/` |
| **旧 RSS 兼容** | `/feed/` 路由保留，内部逻辑保持不变 |
| **Django Admin** | 管理员可正常管理所有用户的内容，不受用户隔离限制 |
| **Django 版本** | 保持 Django 6.x，不引入不兼容变更 |

### 5.5 可扩展性约束

| 维度 | 要求 |
|------|------|
| **关注/粉丝系统（预留）** | User 模型的关注关系（自引用 M2M through 中间表）预留字段但不在本里程碑实现 |
| **评论系统（预留）** | Post 的评论关系预留；当前阶段不实现 |
| **API 接口（预留）** | Views 逻辑尽量抽离为 Service 层或 Manager 方法，便于后续 DRF 改造 |
| **国际化（预留）** | 当前硬编码中文字符串暂不处理 i18n，但 View 和 Form 中的用户可见字符串应统一放到常量或 `verbose_name` 中 |
| **多租户（远期）** | 行级归属架构为未来多租户（如组织/团队博客）打下基础 |

---

## 6. 实施路线建议

### 6.1 分阶段实施

```
Phase 1: 数据模型重构（1-2 天）
├── 新增 UserProfile 模型 + 迁移
├── Category/Tag/Series/Memo 增加 author 字段 + 数据迁移
├── 新增联合唯一约束 + 迁移
├── 新增数据库索引 + 迁移
└── seed_data 适配

Phase 2: 认证与邀请注册（1-2 天）
├── InviteCode 模型 + 迁移
├── 邀请码生成页（含频率限制逻辑）
├── 邀请码注册页（校验 + 核销 + 回填邀请人 + 自动登录）
├── UserProfile 自动创建（post_save signal）
├── 登录/登出保持不变
├── 个人资料编辑页
├── 过期邀请码清理命令
└── 邀请关系在 Admin 中可查询

Phase 3: URL 路由 + 视图重构（2-3 天）
├── 实现 AuthorRequiredMixin / AuthorOrPublishedMixin
├── 所有 View 的 author 过滤
├── URL 结构切换（/@username/ 前缀）
├── 用户主页 UserSpaceView
└── 旧 URL 301 重定向兼容

Phase 4: 模板重构（2 天）
├── profile_card → 动态用户资料
├── nav → 用户空间导航
├── 文章卡片 → 作者链接
├── 新增 user_space.html / register.html / profile_edit.html
└── footer 动态化

Phase 5: RSS / Sitemap / 搜索（1 天）
├── 用户级 RSS Feed
├── Sitemap 更新
└── 搜索结果增加作者信息

Phase 6: 测试 + 安全审计（1-2 天）
├── 权限越权测试
├── 数据迁移回滚测试
├── 并发创建 slug 冲突测试
└── 性能基准测试
```

### 6.2 关键风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 数据迁移失败 | 历史数据丢失 | 迁移前备份 SQLite；编写可逆的 RunPython 迁移 |
| Slug 唯一性冲突 | 迁移失败 | 迁移脚本中处理冲突 slug（追加作者 ID 后缀） |
| 旧 URL 失效 | 外链/书签失效 | 保留旧 URL 模式的重定向逻辑至少 3 个月 |
| 性能退化 | 首页变慢 | 添加复合索引；保持 select_related/prefetch_related |

---

> **文档结束。后续变更请通过版本号追踪。**
