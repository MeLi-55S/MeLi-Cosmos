import uuid
import os

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

    status = models.CharField('文章状态', max_length=10, choices=STATUS_CHOICES, default='published')
    created_time = models.DateTimeField('创建时间', default=timezone.now)
    modified_time = models.DateTimeField('修改时间', auto_now=True)
    views = models.PositiveIntegerField('浏览量', default=0)

    class Meta:
        verbose_name = '文章'
        verbose_name_plural = verbose_name
        ordering = ['-created_time']
        constraints = [
            models.UniqueConstraint(fields=['slug', 'author'], name='unique_post_per_author'),
        ]
        indexes = [
            models.Index(fields=['author', 'slug']),
            models.Index(fields=['author', 'status', '-created_time']),
            models.Index(fields=['status', '-created_time']),
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
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='评论者')
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    content = models.TextField('评论内容')
    created_time = models.DateTimeField('评论时间', auto_now_add=True)
    modified_time = models.DateTimeField('修改时间', auto_now=True)

    class Meta:
        verbose_name = '评论'
        verbose_name_plural = verbose_name
        ordering = ['created_time']
        indexes = [
            models.Index(fields=['content_type', 'object_id', 'created_time']),
        ]

    def __str__(self):
        return f'{self.user.username}: {self.content[:30]}'


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

    url = f"https://api.dicebear.com/7.x/bottts/png?seed={profile.user.username}&size=256"
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
