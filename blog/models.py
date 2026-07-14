import uuid

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User


class Category(models.Model):
    name = models.CharField('分类名称', max_length=50, unique=True)
    slug = models.SlugField('URL别名', max_length=50, unique=True, blank=True)

    class Meta:
        verbose_name = '分类'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)


class Tag(models.Model):
    name = models.CharField('标签名称', max_length=50, unique=True)
    slug = models.SlugField('URL别名', max_length=50, unique=True, blank=True)

    class Meta:
        verbose_name = '标签'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)


class Post(models.Model):
    STATUS_CHOICES = (
        ('draft', '草稿'),
        ('published', '已发布'),
        ('private', '私密'),
    )

    # Core identification
    title = models.CharField('文章标题', max_length=200)
    slug = models.SlugField('URL别名', max_length=200, unique=True, blank=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='作者')

    # Content (Pure Markdown Input)
    cover = models.URLField('封面图片URL', max_length=500, blank=True)
    body = models.TextField('文章正文')
    excerpt = models.CharField('文章摘要', max_length=500, blank=True)

    # Ingestion Anchor: Unique UUID to bind local Obsidian cards with cloud DB
    unique_id = models.UUIDField('外部同步UUID', default=uuid.uuid4, unique=True, editable=False)

    # Relations
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='分类')
    tags = models.ManyToManyField(Tag, blank=True, verbose_name='标签')
    series = models.ForeignKey("Series", on_delete=models.SET_NULL, null=True, blank=True, verbose_name='所属系列')
    series_order = models.PositiveSmallIntegerField('系列序号', default=1)

    # Lifecycle & Stats
    status = models.CharField('文章状态', max_length=10, choices=STATUS_CHOICES, default='draft')
    created_time = models.DateTimeField('创建时间', default=timezone.now)
    modified_time = models.DateTimeField('修改时间', auto_now=True)
    views = models.PositiveIntegerField('浏览量', default=0)

    class Meta:
        verbose_name = '文章'
        verbose_name_plural = verbose_name
        ordering = ['-created_time']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # Fallback for excerpt: auto-truncating markdown formatting logic
        if not self.excerpt:
            self.excerpt = self.body[:150] + '...'
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)


class Series(models.Model):
    """Organize posts into a series/tutorial sequence."""
    name = models.CharField('系列名称', max_length=100, unique=True)
    slug = models.SlugField('URL别名', max_length=100, unique=True, blank=True)
    description = models.TextField('系列描述', blank=True)

    class Meta:
        verbose_name = '文章系列'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name, allow_unicode=True)
        super().save(*args, **kwargs)


class Memo(models.Model):
    """Microblogging / Cyber Treehole component for short updates."""
    content = models.TextField('碎碎念内容')
    created_time = models.DateTimeField('发布时间', auto_now_add=True)
    is_public = models.BooleanField('是否公开', default=True)

    class Meta:
        verbose_name = '碎碎念'
        verbose_name_plural = verbose_name
        ordering = ['-created_time']

    def __str__(self):
        return self.content[:30]
