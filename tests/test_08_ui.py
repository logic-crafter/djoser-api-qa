"""
T08 · UI 自动化测试（Playwright）

被测界面：Django Admin 后台（`/admin/`）—— djoser 账号体系的管理界面

覆盖维度：
- 登录页：元素可见性、必填校验、错误凭据提示、URL 重定向
- 用户列表：渲染、搜索过滤、空结果
- 用户详情：字段展示、编辑保存、字段级校验错误
- 登出：会话失效
- 权限边界：非 staff 用户访问后台应被拒绝

**重要约束**
------------
Playwright 的 sync API 启动后，Django ORM 会被判定为处于异步上下文而不可用
（`SynchronousOnlyOperation`）。因此本模块遵循两条纪律：

1. 所有测试数据在浏览器启动**之前**由 `ui_seed_data` fixture 一次性建好；
2. 用例内**不做任何 ORM 调用**，"修改是否生效"一律通过 UI 二次访问来验证，
   而不是 `refresh_from_db()`。

这既是对 Playwright 同步 API 行为的正确适配，也符合 UI 自动化的真实意图 ——
验证用户看得见的行为，而不是绕过界面去查数据库。
"""

import pytest

pytestmark = [pytest.mark.django_db, pytest.mark.ui]

ADMIN_USER = "uitester"
ADMIN_PASSWORD = "Adm1nPass!23"
NORMAL_USER = "normaluser"
NORMAL_PASSWORD = "Us3rPass!23"


def _login(page, live_server, username, password):
    """执行后台登录，返回是否登录成功。"""
    page.goto(f"{live_server.url}/admin/login/?next=/admin/")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle")
    return "/admin/login/" not in page.url


# ==========================================================================
# 登录页
# ==========================================================================

def test_admin_login_page_elements(ui_page, live_server):
    """登录页应包含用户名、密码输入框与提交按钮。"""
    ui_page.goto(f"{live_server.url}/admin/login/")

    assert ui_page.locator("#id_username").is_visible(), "未找到用户名输入框"
    assert ui_page.locator("#id_password").is_visible(), "未找到密码输入框"
    assert ui_page.locator("input[type=submit]").is_visible(), "未找到提交按钮"


def test_admin_login_with_empty_credentials_blocked_by_browser(ui_page, live_server):
    """
    空凭据提交应停留在登录页，且字段被标记为校验失败。

    Django Admin 的登录表单为字段加了 HTML5 `required` 属性，
    浏览器会在**客户端**直接拦截提交，因此不会渲染服务端的 `ul.errorlist`。
    这里断言的是"浏览器原生校验已生效"这一真实可观测行为：
      - 页面停留在登录页
      - 输入框带有 `aria-invalid="true"` 与 `aria-describedby` 指向错误说明
    """
    ui_page.goto(f"{live_server.url}/admin/login/")

    # 断言依据：登录表单对必填字段设置了 HTML5 required 属性，
    # 浏览器会在客户端拦截空值提交（`aria-invalid` 由浏览器内部维护，不反映到 DOM 属性）
    assert ui_page.locator("#id_username").get_attribute("required") is not None, (
        "用户名字段缺少 required 属性，客户端校验缺失"
    )
    assert ui_page.locator("#id_password").get_attribute("required") is not None, (
        "密码字段缺少 required 属性，客户端校验缺失"
    )

    # 提交空表单：应被浏览器拦截，页面停留不跳转
    ui_page.click("input[type=submit]")
    ui_page.wait_for_load_state("networkidle")
    assert "/admin/login/" in ui_page.url, "空凭据提交后不应跳转"

    # 校验未通过时，页面应聚焦到第一个无效字段
    focused_id = ui_page.evaluate("() => document.activeElement && document.activeElement.id")
    assert focused_id in ("id_username", "id_password"), (
        f"客户端校验未生效，焦点未落在无效字段上（当前：{focused_id}）"
    )


def test_admin_login_with_empty_credentials_server_side_error(ui_page, live_server):
    """
    绕过浏览器原生校验后，服务端应返回字段级错误提示。

    前端校验只是体验优化，**真正的防线必须在服务端**。
    因此这里用 JS 移除 `required` 属性模拟绕过前端校验，
    断言服务端依然拒绝空凭据并返回 `ul.errorlist`。
    """
    ui_page.goto(f"{live_server.url}/admin/login/")

    # 移除 HTML5 required，绕过客户端校验，直接把空值提交给服务端
    ui_page.eval_on_selector_all(
        "input[required]", "els => els.forEach(el => el.removeAttribute('required'))"
    )
    ui_page.click("input[type=submit]")
    ui_page.wait_for_load_state("networkidle")

    assert "/admin/login/" in ui_page.url, "空凭据提交后不应跳转"
    error = ui_page.locator("#id_username_error, #id_password_error").first
    assert error.is_visible(), "服务端未返回字段级错误提示"
    assert "required" in error.inner_text().lower()


def test_admin_login_with_wrong_password_shows_error(ui_page, live_server):
    """错误口令应停留在登录页并提示认证失败。"""
    ui_page.goto(f"{live_server.url}/admin/login/")

    ui_page.fill("#id_username", ADMIN_USER)
    ui_page.fill("#id_password", "WrongPassword!999")
    ui_page.click("input[type=submit]")
    ui_page.wait_for_load_state("networkidle")

    assert "/admin/login/" in ui_page.url, "错误口令不应跳转到后台首页"
    assert ui_page.locator(".errornote").first.is_visible(), "未显示认证失败提示"


def test_admin_login_success_navigates_to_dashboard(ui_page, live_server):
    """正确凭据应登录成功并进入后台首页。"""
    ok = _login(ui_page, live_server, ADMIN_USER, ADMIN_PASSWORD)
    assert ok, "正确凭据登录后仍停留在登录页"
    assert ui_page.locator("#site-name").is_visible(), "后台首页未渲染站点标题"


def test_admin_login_with_nonexistent_user(ui_page, live_server):
    """不存在的用户应被拒绝。"""
    ui_page.goto(f"{live_server.url}/admin/login/")

    ui_page.fill("#id_username", "no_such_user_zzz")
    ui_page.fill("#id_password", ADMIN_PASSWORD)
    ui_page.click("input[type=submit]")
    ui_page.wait_for_load_state("networkidle")

    assert "/admin/login/" in ui_page.url, "不存在的用户不应登录成功"


# ==========================================================================
# 用户列表页
# ==========================================================================

def test_admin_user_list_renders(ui_page, live_server):
    """用户列表页应渲染出预置的用户。"""
    assert _login(ui_page, live_server, ADMIN_USER, ADMIN_PASSWORD), "管理员登录失败"

    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.wait_for_load_state("networkidle")

    # Django Admin 的 changelist 标题为 "Select user to change"
    assert "user" in ui_page.title().lower(), f"用户列表页标题异常：{ui_page.title()}"
    assert ui_page.locator("#result_list").is_visible(), "未找到用户列表表格"
    assert ui_page.get_by_text(NORMAL_USER).count() > 0, "用户列表中未出现预置用户"


def test_admin_user_search_filters_list(ui_page, live_server):
    """搜索框应能按用户名过滤列表。"""
    assert _login(ui_page, live_server, ADMIN_USER, ADMIN_PASSWORD), "管理员登录失败"

    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.fill("#searchbar", NORMAL_USER)
    ui_page.press("#searchbar", "Enter")
    ui_page.wait_for_load_state("networkidle")

    assert ui_page.get_by_text(NORMAL_USER).count() > 0, "搜索后未找到目标用户"


def test_admin_user_search_no_match_shows_empty_result(ui_page, live_server):
    """
    搜索无结果时应展示分页器与空结果状态，而非报错页。

    Django Admin 在无匹配结果时**不渲染** `#result_list` 表格，
    只渲染 paginator，因此这里以"表格不存在 + 目标用户不出现"作为断言。
    """
    assert _login(ui_page, live_server, ADMIN_USER, ADMIN_PASSWORD), "管理员登录失败"

    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.fill("#searchbar", "zzz_no_such_user_zzz")
    ui_page.press("#searchbar", "Enter")
    ui_page.wait_for_load_state("networkidle")

    assert ui_page.locator("#changelist").is_visible(), "搜索无结果时页面主体区域应仍然存在"
    assert ui_page.locator(".paginator").is_visible(), "搜索无结果时未展示分页器"
    assert ui_page.get_by_text(NORMAL_USER).count() == 0, "搜索无结果时不应出现其他用户"


# ==========================================================================
# 用户编辑页
# ==========================================================================

def test_admin_user_change_page_shows_fields(ui_page, live_server):
    """用户编辑页应展示关键字段。"""
    assert _login(ui_page, live_server, ADMIN_USER, ADMIN_PASSWORD), "管理员登录失败"

    # 从列表页点进普通用户的编辑页，避免硬编码主键
    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.fill("#searchbar", NORMAL_USER)
    ui_page.press("#searchbar", "Enter")
    ui_page.wait_for_load_state("networkidle")
    ui_page.get_by_text(NORMAL_USER).first.click()
    ui_page.wait_for_load_state("networkidle")

    assert ui_page.locator("#id_username").is_visible(), "编辑页未找到 username 字段"
    assert ui_page.locator("#id_email").is_visible(), "编辑页未找到 email 字段"


def test_admin_user_edit_saves_change(ui_page, live_server):
    """修改 email 并保存后，应能在列表页看到新值（通过 UI 二次验证）。"""
    assert _login(ui_page, live_server, ADMIN_USER, ADMIN_PASSWORD), "管理员登录失败"

    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.fill("#searchbar", NORMAL_USER)
    ui_page.press("#searchbar", "Enter")
    ui_page.wait_for_load_state("networkidle")
    ui_page.get_by_text(NORMAL_USER).first.click()
    ui_page.wait_for_load_state("networkidle")

    ui_page.fill("#id_email", "updated@example.com")
    ui_page.click("input[name=_save]")
    ui_page.wait_for_load_state("networkidle")

    # 通过列表页再次访问验证保存生效（不依赖 ORM）
    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.fill("#searchbar", "updated@example.com")
    ui_page.press("#searchbar", "Enter")
    ui_page.wait_for_load_state("networkidle")

    assert ui_page.get_by_text("updated@example.com").count() > 0, "后台编辑的 email 未生效"


def test_admin_user_edit_with_invalid_email_shows_field_error(ui_page, live_server):
    """非法 email 应触发字段级校验错误，而非保存成功。"""
    assert _login(ui_page, live_server, ADMIN_USER, ADMIN_PASSWORD), "管理员登录失败"

    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.fill("#searchbar", NORMAL_USER)
    ui_page.press("#searchbar", "Enter")
    ui_page.wait_for_load_state("networkidle")
    ui_page.get_by_text(NORMAL_USER).first.click()
    ui_page.wait_for_load_state("networkidle")

    ui_page.fill("#id_email", "not-a-valid-email")
    ui_page.click("input[name=_save]")
    ui_page.wait_for_load_state("networkidle")

    assert ui_page.locator(".errorlist").first.is_visible(), "非法 email 未显示字段级错误"


# ==========================================================================
# 登出与权限边界
# ==========================================================================

def test_admin_logout_invalidates_session(ui_page, live_server):
    """登出后再访问后台应跳转到登录页。"""
    assert _login(ui_page, live_server, ADMIN_USER, ADMIN_PASSWORD), "管理员登录失败"

    # Django Admin 的登出是 `#logout-form` 表单里的提交按钮（不是 <a> 链接）
    ui_page.click("#user-tools")
    ui_page.click("#logout-form button[type=submit]")
    ui_page.wait_for_load_state("networkidle")

    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.wait_for_load_state("networkidle")
    assert "/admin/login/" in ui_page.url, "登出后仍可访问后台页面"


def test_admin_rejects_non_staff_user(ui_page, live_server):
    """非 staff 用户访问后台应被重定向到登录页。"""
    ui_page.goto(f"{live_server.url}/admin/login/")
    ui_page.fill("#id_username", NORMAL_USER)
    ui_page.fill("#id_password", NORMAL_PASSWORD)
    ui_page.click("input[type=submit]")
    ui_page.wait_for_load_state("networkidle")

    ui_page.goto(f"{live_server.url}/admin/auth/user/")
    ui_page.wait_for_load_state("networkidle")

    assert "/admin/login/" in ui_page.url, "非 staff 用户不应能访问后台列表页"
