"""API 的请求/响应模型（pydantic / django-ninja Schema）。

命名约定：``*Out`` 为响应体，``*In`` 为请求体。时间字段一律 ISO-8601。
类按依赖顺序排列（先引用型小结构，再实体，再分页信封，最后请求体）。
"""

from datetime import datetime
from typing import List, Optional

from ninja import Schema

# ── 引用型小结构 ────────────────────────────────────────────────────────


class AuthorRef(Schema):
    username: str
    display_name: str
    avatar_url: str


class CategoryRef(Schema):
    id: int
    name: str
    slug: str


class CategoryOut(CategoryRef):
    post_count: int = 0


class TagRef(Schema):
    id: int
    name: str
    slug: str


class TagOut(TagRef):
    post_count: int = 0


class SeriesRef(Schema):
    id: int
    name: str
    slug: str


class SeriesOut(SeriesRef):
    description: str = ""
    author: Optional[str] = None
    post_count: int = 0


class LikeState(Schema):
    count: int
    display_text: str
    user_liked: bool


class SeriesNavItem(Schema):
    """系列前后篇的轻量表示（只含导航需要的字段）。"""

    id: int
    slug: str
    title: str
    url: str
    series_order: int


# ── 实体 ────────────────────────────────────────────────────────────────


class PostBriefOut(Schema):
    id: int
    unique_id: str
    slug: str
    title: str
    excerpt: str
    cover: str = ""
    status: str
    license: str
    views: int
    read_minutes: int
    url: str
    created_time: datetime
    modified_time: datetime
    author: AuthorRef
    category: Optional[CategoryRef] = None
    series: Optional[SeriesRef] = None
    tags: List[TagRef] = []


class SeriesNav(Schema):
    series: SeriesRef
    index: int
    total: int
    prev: Optional[SeriesNavItem] = None
    next: Optional[SeriesNavItem] = None


class PostDetailOut(PostBriefOut):
    body: str
    body_html: str
    toc_html: str = ""
    likes: LikeState
    comments_count: int
    related: List[PostBriefOut] = []
    series_nav: Optional[SeriesNav] = None


class MemoOut(Schema):
    id: int
    content: str
    is_public: bool
    url: str
    author: AuthorRef
    created_time: datetime
    likes: LikeState
    comments_count: int


class CommentOut(Schema):
    id: int
    content: str
    display_name: str
    avatar_url: str
    is_guest: bool
    is_visible: bool
    is_pending: bool
    created_time: datetime
    content_type: str
    object_id: int


class NotificationOut(Schema):
    id: int
    notification_type: str
    type_display: str
    message: str
    is_read: bool
    created_time: datetime
    target_url: Optional[str] = None
    content_type: str
    object_id: int
    actor: Optional[AuthorRef] = None


class UserPublicOut(Schema):
    username: str
    display_name: str
    title: str = ""
    bio: str = ""
    avatar_url: str
    website: str = ""
    github: str = ""
    mastodon: str = ""
    url: str
    joined_at: Optional[datetime] = None
    stats: dict


class InviteCodeOut(Schema):
    code: str
    created_at: datetime
    expires_at: datetime
    is_used: bool
    is_expired: bool
    invitee: Optional[str] = None


class TokenOut(Schema):
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class TokenCreatedOut(TokenOut):
    token: str
    hint: str


class AuthenticatedUserOut(Schema):
    username: str
    display_name: str
    is_staff: bool
    avatar_url: str
    url: str
    token: Optional[TokenOut] = None


class UploadOut(Schema):
    url: str
    name: str
    size: int
    width: int
    height: int
    dedup: bool


class AvatarOut(Schema):
    url: str
    width: int
    height: int


class SiteMetaOut(Schema):
    site_name: str
    display_name: str
    base_url: str
    api_version: str
    docs_url: str
    totals: dict
    auth: dict
    features: List[str]


class OkOut(Schema):
    ok: bool = True
    detail: Optional[str] = None


class HealthOut(Schema):
    status: str
    database: str
    server_time: datetime
    django_version: str
    api_version: str


# ── 分页信封 ────────────────────────────────────────────────────────────


class PostListOut(Schema):
    count: int
    page: int
    page_size: int
    total_pages: int
    results: List[PostBriefOut]


class MemoListOut(Schema):
    count: int
    page: int
    page_size: int
    total_pages: int
    results: List[MemoOut]


class CommentListOut(Schema):
    count: int
    page: int
    page_size: int
    total_pages: int
    results: List[CommentOut]


class NotificationListOut(Schema):
    count: int
    page: int
    page_size: int
    total_pages: int
    unread: int
    results: List[NotificationOut]


class UserListOut(Schema):
    count: int
    page: int
    page_size: int
    total_pages: int
    results: List[UserPublicOut]


class CategoryListOut(Schema):
    count: int
    results: List[CategoryOut]


class TagListOut(Schema):
    count: int
    results: List[TagOut]


class SeriesListOut(Schema):
    count: int
    results: List[SeriesOut]


class TokenListOut(Schema):
    count: int
    results: List[TokenOut]


class InviteListOut(Schema):
    count: int
    results: List[InviteCodeOut]


class ArchiveBucketOut(Schema):
    year: int
    month: int
    label: str
    count: int
    posts: List[PostBriefOut]


class ArchiveListOut(Schema):
    count: int
    buckets: List[ArchiveBucketOut]


# ── 请求体 ──────────────────────────────────────────────────────────────


class LoginIn(Schema):
    username: str
    password: str
    device_name: Optional[str] = "mobile"


class RegisterIn(Schema):
    code: str
    username: str
    password1: str
    password2: str
    device_name: Optional[str] = "mobile"


class TokenNameIn(Schema):
    name: str = "mobile"


class PostIn(Schema):
    """创建 / 更新文章。更新时省略的字段表示不改动。"""

    title: Optional[str] = None
    body: Optional[str] = None
    cover: Optional[str] = None
    excerpt: Optional[str] = None
    category_id: Optional[int] = None
    series_id: Optional[int] = None
    series_order: Optional[int] = None
    license: Optional[str] = None
    status: Optional[str] = None
    tag_names: Optional[List[str]] = None


class MemoIn(Schema):
    content: str
    is_public: bool = True


class CommentIn(Schema):
    content_type: str
    object_id: int
    content: str
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    website: Optional[str] = None  # 蜜罐字段，非空即视为机器人


class LikeIn(Schema):
    content_type: str
    object_id: int


class LikeOut(Schema):
    liked: bool
    count: int
    display_text: str


class NameIn(Schema):
    name: str


class SeriesIn(Schema):
    name: str
    description: Optional[str] = ""


class SeriesPostIn(Schema):
    action: str  # add | remove
    post_id: int


class ProfileIn(Schema):
    display_name: Optional[str] = None
    title: Optional[str] = None
    bio: Optional[str] = None
    website: Optional[str] = None
    github: Optional[str] = None
    email: Optional[str] = None


# ── 浏览计数 ────────────────────────────────────────────────────────────


class ViewIn(Schema):
    fingerprint: str


class ViewOut(Schema):
    counted: bool
    views: int


# ── 搜索 ────────────────────────────────────────────────────────────────


class SearchOut(Schema):
    query: str
    posts: List[PostBriefOut] = []
    memos: List[MemoOut] = []
    users: List[AuthorRef] = []
    categories: List[CategoryOut] = []
    tags: List[TagOut] = []
    series: List[SeriesOut] = []


# ── 评论 / 点赞 ─────────────────────────────────────────────────────────


class LikeStateMapOut(Schema):
    """``{"blog.post:12": {count, display_text, user_liked}}``，移动端列表页一次拿全。"""

    states: dict


class MarkReadIn(Schema):
    ids: Optional[List[int]] = None
    mark_all: bool = False


class MarkReadOut(Schema):
    updated: int
    unread: int


# ── 上传 ────────────────────────────────────────────────────────────────


class UploadItemOut(Schema):
    id: str
    url: str
    name: str
    size: int
    width: int
    height: int
    created_time: datetime


class UploadListOut(Schema):
    count: int
    page: int
    page_size: int
    total_pages: int
    results: List[UploadItemOut]


# ── 账号 ────────────────────────────────────────────────────────────────


class ProfileOut(Schema):
    username: str
    display_name: str
    title: str = ""
    bio: str = ""
    website: str = ""
    github: str = ""
    github_username: str = ""
    mastodon: str = ""
    email: str = ""
    avatar_url: str
    url: str
    is_staff: bool
    joined_at: datetime
    stats: dict


class LoginOut(TokenCreatedOut):
    user: ProfileOut


class InviteOut(InviteCodeOut):
    url: str = ""


class UnreadOut(Schema):
    """收件箱角标：移动端轮询用的最小响应。"""

    unread: int
