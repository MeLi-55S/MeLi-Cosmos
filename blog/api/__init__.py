"""MeLi Cosmos 的 REST API 层（django-ninja）。

挂载点：``/api/v1/``（见 ``my_cosmos/urls.py``），Swagger 文档在 ``/api/v1/docs``。

设计约定：
- 读端点全部匿名可用，只返回 ``status='published'`` 的文章与 ``is_public=True`` 的碎碎念；
  带有效令牌访问时，作者本人可额外看到自己的草稿与私密内容。
- 写端点一律需要 ``Authorization: Bearer mlc_<hex>``（``ApiToken``，见 ``blog.models``）。
- 业务规则不重新实现：markdown 渲染、点赞文案、限流、图片处理等全部复用 ``blog/views.py``
  与 ``blog/forms.py`` 里的既有函数，避免出现第二套逻辑。
"""
