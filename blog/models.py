import uuid
import os
import hashlib

from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


def _unique_slug(model_cls, base_slug, author, exclude_pk=None):
    """Return a unique slug for the given author by appending -2, -3, etc."""
    from django.utils.text import slugify
    slug = slugify(base_slug, allow_unicode=True)
    if not slug:
        return None
    candidate = slug
    n = 2
    while True:
        qs = model_cls.objects.filter(slug=candidate, author=author)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        if not qs.exists():
            return candidate
        candidate = f'{slug}-{n}'
        n += 1


class Category(models.Model):
    name = models.CharField('分类名称', max_length=50)
    slug = models.SlugField('URL别名', max_length=50, blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='作者')

    class Meta:
        verbose_name = '分类'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=['slug', 'author'], name='unique_category_per_author'),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Category, self.name, self.author) or f'category-{uuid.uuid4().hex[:8]}'
        super().save(*args, **kwargs)


class Tag(models.Model):
    name = models.CharField('标签名称', max_length=50)
    slug = models.SlugField('URL别名', max_length=50, blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='作者')

    class Meta:
        verbose_name = '标签'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=['slug', 'author'], name='unique_tag_per_author'),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Tag, self.name, self.author) or f'tag-{uuid.uuid4().hex[:8]}'
        super().save(*args, **kwargs)


class Post(models.Model):
    STATUS_CHOICES = (
        ('draft', '草稿'),
        ('published', '公开发表'),
        ('private', '私密'),
    )

    title = models.CharField('文章标题', max_length=200)
    slug = models.SlugField('URL别名', max_length=200, blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='作者')

    cover = models.URLField('封面图片URL', max_length=500, blank=True)
    body = models.TextField('文章正文')
    excerpt = models.CharField('文章摘要', max_length=500, blank=True)

    unique_id = models.UUIDField('外部同步UUID', default=uuid.uuid4, unique=True, editable=False)

    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='分类')
    tags = models.ManyToManyField(Tag, blank=True, verbose_name='标签')
    series = models.ForeignKey("Series", on_delete=models.SET_NULL, null=True, blank=True, verbose_name='所属系列')
    series_order = models.PositiveSmallIntegerField('系列序号', default=1)

    LICENSE_CHOICES = (
        ('CC BY-NC 4.0', 'CC BY-NC 4.0（署名-非商业性使用）'),
        ('CC BY 4.0', 'CC BY 4.0（署名）'),
        ('CC BY-SA 4.0', 'CC BY-SA 4.0（署名-相同方式共享）'),
        ('CC BY-NC-SA 4.0', 'CC BY-NC-SA 4.0（署名-非商业-相同方式共享）'),
        ('CC BY-ND 4.0', 'CC BY-ND 4.0（署名-禁止演绎）'),
        ('CC BY-NC-ND 4.0', 'CC BY-NC-ND 4.0（署名-非商业-禁止演绎）'),
        ('CC0', 'CC0（公共领域）'),
    )
    license = models.CharField('许可协议', max_length=30, choices=LICENSE_CHOICES, default='CC BY-NC 4.0')
    status = models.CharField('文章状态', max_length=10, choices=STATUS_CHOICES, default='published')
    created_time = models.DateTimeField('创建时间', default=timezone.now)
    modified_time = models.DateTimeField('修改时间', auto_now=True)
    views = models.PositiveIntegerField('浏览量', default=0)

    class Meta:
        verbose_name = '文章'
        verbose_name_plural = verbose_name
        ordering = ['-modified_time']
        constraints = [
            models.UniqueConstraint(fields=['slug', 'author'], name='unique_post_per_author'),
        ]
        indexes = [
            models.Index(fields=['author', 'slug']),
            models.Index(fields=['author', 'status', '-modified_time']),
            models.Index(fields=['status', '-modified_time']),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.excerpt and self.body:
            self.excerpt = self.body[:150] + ('...' if len(self.body) > 150 else '')
        if not self.slug:
            self.slug = _unique_slug(Post, self.title, self.author, exclude_pk=self.pk) or f'post-{uuid.uuid4().hex[:8]}'
        super().save(*args, **kwargs)


class Series(models.Model):
    name = models.CharField('系列名称', max_length=100)
    slug = models.SlugField('URL别名', max_length=100, blank=True)
    description = models.TextField('系列描述', blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='作者')

    class Meta:
        verbose_name = '文章系列'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=['slug', 'author'], name='unique_series_per_author'),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Series, self.name, self.author) or f'series-{uuid.uuid4().hex[:8]}'
        super().save(*args, **kwargs)


class Memo(models.Model):
    content = models.TextField('碎碎念内容')
    created_time = models.DateTimeField('发布时间', auto_now_add=True)
    is_public = models.BooleanField('是否公开', default=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='作者')

    class Meta:
        verbose_name = '碎碎念'
        verbose_name_plural = verbose_name
        ordering = ['-created_time']

    def __str__(self):
        return self.content[:30]


def avatar_upload_path(instance, filename):
    return f"avatars/{instance.user.username}.webp"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    display_name = models.CharField('显示名称', max_length=100, blank=True)
    title = models.CharField('头衔', max_length=200, blank=True)
    bio = models.TextField('个人简介', blank=True)
    avatar = models.ImageField('头像', upload_to=avatar_upload_path, blank=True)
    avatar_url = models.URLField('头像URL(旧)', max_length=500, blank=True)
    website = models.URLField('个人网站', max_length=500, blank=True)
    github = models.URLField('GitHub', max_length=500, blank=True)
    mastodon = models.URLField('Mastodon', max_length=500, blank=True)
    # Ban system
    is_banned = models.BooleanField('是否封禁', default=False)
    is_permanent_ban = models.BooleanField('永久封禁', default=False,
        help_text='永久封禁不受递归解封影响，只能直接取消')
    banned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='banned_users', verbose_name='封禁执行人')
    banned_reason = models.TextField('封禁原因', blank=True)
    banned_at = models.DateTimeField('封禁时间', null=True, blank=True)

    class Meta:
        verbose_name = '用户资料'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'{self.user.username} 的资料'

    @property
    def avatar_seed(self):
        return self.user.username

    @property
    def github_username(self):
        if not self.github:
            return ""
        try:
            return self.github.rstrip('/').split('/')[-1]
        except (IndexError, AttributeError):
            return ""


class InviteCode(models.Model):
    code = models.CharField('邀请码', max_length=32, unique=True)
    inviter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_invites', verbose_name='邀请人')
    invitee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='received_invite', verbose_name='被邀请人')
    is_used = models.BooleanField('是否已使用', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    expires_at = models.DateTimeField('过期时间')
    used_at = models.DateTimeField('使用时间', null=True, blank=True)

    class Meta:
        verbose_name = '邀请码'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'邀请码 {self.code} (来自 {self.inviter.username})'

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @classmethod
    def generate_code(cls):
        return os.urandom(16).hex()


class BanAppeal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='申诉用户')
    content = models.TextField('申诉内容')
    created_at = models.DateTimeField('提交时间', auto_now_add=True)
    is_resolved = models.BooleanField('是否已处理', default=False)
    resolved_at = models.DateTimeField('处理时间', null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resolved_appeals', verbose_name='处理人')

    class Meta:
        verbose_name = '封禁申诉'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(is_resolved=False),
                name='unique_pending_appeal_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.user.username} 的申诉'


def upload_image_path(instance, filename):
    return f"uploads/images/{instance.id}.webp"


class UploadedImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    image = models.ImageField("图片文件", upload_to=upload_image_path)
    original_filename = models.CharField("原始文件名", max_length=255)
    md5_hash = models.CharField("MD5哈希", max_length=32, unique=True)
    sha1_hash = models.CharField("SHA1哈希", max_length=40)
    file_size = models.PositiveIntegerField("文件大小(bytes)")
    width = models.PositiveSmallIntegerField("宽度(px)")
    height = models.PositiveSmallIntegerField("高度(px)")
    uploader = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="上传者")
    created_time = models.DateTimeField("上传时间", auto_now_add=True)

    class Meta:
        verbose_name = "上传图片"
        verbose_name_plural = verbose_name
        ordering = ["-created_time"]

    def __str__(self):
        return self.original_filename


class ViewLog(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, verbose_name='文章')
    fingerprint_hash = models.CharField('浏览器指纹哈希', max_length=64)
    ip_hash = models.CharField('IP哈希', max_length=64)
    created_at = models.DateTimeField('访问时间', auto_now_add=True)

    class Meta:
        verbose_name = '浏览记录'
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(
                fields=['post', 'fingerprint_hash', 'ip_hash', 'created_at'],
                name='idx_viewlog_lookup',
            ),
        ]

    def __str__(self):
        return f'{self.post.title} @ {self.created_at:%Y-%m-%d %H:%M}'


class Like(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='用户')
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    created_time = models.DateTimeField('点赞时间', auto_now_add=True)

    class Meta:
        verbose_name = '点赞'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'content_type', 'object_id'],
                name='unique_like_per_user_and_object',
            ),
        ]
        indexes = [
            models.Index(fields=['content_type', 'object_id', '-created_time']),
        ]

    def __str__(self):
        return f'{self.user.username} 赞了 {self.content_object}'


class Comment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='评论者', null=True, blank=True)
    guest_name = models.CharField('游客昵称', max_length=50, blank=True)
    guest_email = models.EmailField('游客邮箱', max_length=254, blank=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    content = models.TextField('评论内容')
    is_visible = models.BooleanField('可见', default=True)
    created_time = models.DateTimeField('评论时间', auto_now_add=True)
    modified_time = models.DateTimeField('修改时间', auto_now=True)

    class Meta:
        verbose_name = '评论'
        verbose_name_plural = verbose_name
        ordering = ['created_time']
        indexes = [
            models.Index(fields=['content_type', 'object_id', 'created_time']),
            models.Index(fields=['guest_email', 'is_visible']),
            models.Index(fields=['is_visible']),
        ]

    @property
    def display_name(self):
        if self.user:
            return self.user.profile.display_name or self.user.username
        return self.guest_name or '匿名'

    @property
    def display_avatar(self):
        if self.user and self.user.profile.avatar:
            return self.user.profile.avatar.url
        if self.user:
            from urllib.parse import quote
            return f'https://api.dicebear.com/7.x/bottts/png?seed={quote(self.user.username)}'
        # Guest: locally-generated avatar, never calls external API
        return get_or_create_guest_avatar(self.guest_email, self.guest_name) or self._fallback_avatar()

    @staticmethod
    def _fallback_avatar():
        """Last-resort placeholder when avatar generation fails."""
        return "https://api.dicebear.com/7.x/bottts/png?seed=guest"

    def __str__(self):
        name = self.user.username if self.user else (self.guest_name or '游客')
        return f'{name}: {self.content[:30]}'


class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('comment', '评论'),
        ('reply', '回复'),
        ('like', '点赞'),
        ('system', '系统通知'),
    )

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications', verbose_name='接收人')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='actor_notifications', verbose_name='触发者')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, verbose_name='通知类型')
    message = models.TextField(verbose_name='通知内容')
    is_read = models.BooleanField(default=False, verbose_name='是否已读')
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, verbose_name='内容类型')
    object_id = models.PositiveIntegerField(verbose_name='内容ID')
    content_object = GenericForeignKey('content_type', 'object_id')
    created_time = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        verbose_name = '通知'
        verbose_name_plural = '通知'
        ordering = ['-created_time']
        indexes = [
            models.Index(fields=['recipient', '-created_time']),
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['content_type', 'object_id']),
        ]

    def __str__(self):
        return f'[{self.get_notification_type_display()}] 给 {self.recipient.username}: {self.message[:30]}'

    def get_target_url(self):
        """Return the URL to the related content object (Post, Memo, etc.)."""
        obj = self.content_object
        if obj is None:
            return None
        from django.urls import reverse
        if hasattr(obj, 'slug') and hasattr(obj, 'author'):
            return reverse('post_detail', kwargs={'username': obj.author.username, 'slug': obj.slug})
        if hasattr(obj, 'author'):
            return reverse('memo_detail', kwargs={'username': obj.author.username, 'pk': obj.pk})
        return None


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        profile = UserProfile.objects.create(user=instance, display_name=instance.username)
        generate_default_avatar(profile)


def generate_default_avatar(profile):
    """Fetch DiceBear PNG for the user, convert to WebP, store locally.
    Best-effort: silently returns False on network/image errors.
    """
    from urllib.request import urlopen, Request
    from urllib.error import URLError
    from io import BytesIO
    from PIL import Image as PILImage
    from django.core.files.base import ContentFile

    from urllib.parse import quote
    url = f"https://api.dicebear.com/7.x/bottts/png?seed={quote(profile.user.username)}&size=256"
    try:
        req = Request(url, headers={"User-Agent": "MeLi Cosmos/1.0"})
        with urlopen(req, timeout=5) as resp:
            raw = resp.read()
    except (URLError, OSError):
        return False

    try:
        img = PILImage.open(BytesIO(raw))
        if img.mode in ("RGBA", "P"):
            rgb = PILImage.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            rgb.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = rgb
        clean = PILImage.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        buf = BytesIO()
        clean.save(buf, format="WEBP", quality=80)
        webp_data = buf.getvalue()
    except Exception:
        return False

    profile.avatar.save(
        f"{profile.user.username}.webp",
        ContentFile(webp_data),
        save=True,
    )
    return True


# ── Guest avatar: fetch from DiceBear API → cache locally → serve directly ──
# Client never calls the external API; the server does it once and caches the result.

_DICEBEAR_URL = "https://api.dicebear.com/7.x/bottts/png?seed={seed}&size=128"

_GUEST_AVATAR_DIR = os.path.join("avatars", "guests")


def _guest_avatar_key(guest_email, guest_name):
    """Deterministic key for guest avatar file. Same guest → same key."""
    seed = f"{guest_email}|{guest_name}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _guest_avatar_seed(guest_email, guest_name):
    """Compose the DiceBear seed: MD5(email) so raw email is never in the URL or filename."""
    import hashlib as _hashlib
    raw = (guest_email or "guest") + "|" + (guest_name or "?")
    return _hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_or_create_guest_avatar(guest_email, guest_name):
    """Fetch a DiceBear avatar on behalf of the guest, convert to WebP, cache
    locally, return the local URL.  Idempotent — returns existing file path on
    repeat calls.  Returns None on failure.
    """
    if not guest_email:
        guest_email = ""
    if not guest_name:
        guest_name = "?"
    key = _guest_avatar_key(guest_email, guest_name)

    filename = f"{key}.webp"
    rel_path = os.path.join(_GUEST_AVATAR_DIR, filename)

    from django.conf import settings
    full_path = os.path.join(settings.MEDIA_ROOT, rel_path)

    # Already cached — serve local file
    if os.path.isfile(full_path):
        return f"{settings.MEDIA_URL}{rel_path}"

    try:
        from urllib.request import urlopen, Request
        from urllib.parse import quote
        from io import BytesIO
        from PIL import Image as PILImage

        seed = _guest_avatar_seed(guest_email, guest_name)
        url = _DICEBEAR_URL.format(seed=quote(seed))

        req = Request(url, headers={"User-Agent": "MeLi Cosmos/1.0"})
        with urlopen(req, timeout=5) as resp:
            raw = resp.read()

        # Convert PNG → WebP (same pipeline as user avatars)
        img = PILImage.open(BytesIO(raw))
        if img.mode in ("RGBA", "P"):
            rgb = PILImage.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            rgb.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = rgb
        clean = PILImage.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        buf = BytesIO()
        clean.save(buf, format="WEBP", quality=80)
        webp_data = buf.getvalue()

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(webp_data)

        return f"{settings.MEDIA_URL}{rel_path}"
    except Exception:
        return None
