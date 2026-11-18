"""
djoser 路由配置。

djoser 的端点分散在三个 urls 模块：
- `djoser.urls`          → UserViewSet 路由（注册、用户 CRUD、改密码、改用户名等）
- `djoser.urls.authtoken` → Token 登录/登出（/token/login/、/token/logout/）

注意：`djoser.urls` **不包含** token 端点，`/users/token/` 并不是有效路径，
登录必须走 `/token/login/`。这是测试中容易踩的第一个坑。

此外挂载 Django Admin 后台（`/admin/`），作为 UI 自动化测试的被测界面。
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/", include("djoser.urls")),
    path("auth/", include("djoser.urls.authtoken")),
]
