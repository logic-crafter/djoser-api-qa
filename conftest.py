"""
全局 fixture 与测试基础设施。

核心设计：
1. `live_server` 提供的真实 HTTP 服务，测试走完整 WSGI 栈（中间件 / 鉴权 / 序列化 / 异常处理），
   断言基于"外部可观测行为"（状态码、响应体），而非数据库直查。
2. 每个用例使用唯一用户名，互不依赖执行顺序，彻底避免数据污染。
3. `client` fixture 封装 Token 获取，统一认证头的拼装，减少用例噪音。
"""

import os
import sys
import uuid

# 把项目根目录加入 sys.path，确保 `testenv` 包可被导入
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

User = get_user_model()

# 统一的测试口令，满足 djoser 默认密码强度策略
VALID_PASSWORD = "Str0ngPass!23"
WEAK_PASSWORD = "123"


def _uid(prefix: str = "u") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


@pytest.fixture
def unique_username() -> str:
    """生成全局唯一用户名，避免用例间数据互相污染。"""
    return _uid()


@pytest.fixture
def unique_email(unique_username: str) -> str:
    return f"{unique_username}@example.com"


@pytest.fixture
def create_user(db):
    """工厂 fixture：创建用户并返回 (user, token_key)。"""

    def _factory(username=None, email=None, password=VALID_PASSWORD, **extra):
        username = username or _uid()
        email = email or f"{username}@example.com"
        user = User.objects.create_user(
            username=username, email=email, password=password, **extra
        )
        token, _ = Token.objects.get_or_create(user=user)
        return user, token.key

    return _factory


@pytest.fixture
def auth_client(live_server, create_user):
    """
    返回一个带认证能力的客户端。

    返回 (client, user, token_key)，client 上已挂载 Token 认证头。
    """
    import requests

    user, token_key = create_user()
    session = requests.Session()
    session.headers.update({"Authorization": f"Token {token_key}"})
    return session, user, token_key


@pytest.fixture
def anon_client(live_server):
    """未认证客户端，用于验证鉴权边界。"""
    import requests

    return requests.Session()


@pytest.fixture
def registered_user(live_server, create_user):
    """已注册但未认证的账号，仅返回凭据，供登录类用例使用。"""
    user, _ = create_user()
    return {
        "username": user.username,
        "email": user.email,
        "password": VALID_PASSWORD,
    }


@pytest.fixture
def server_base_url(live_server) -> str:
    """
    服务根地址。

    命名注意：不能用 `base_url` —— pytest-playwright 插件已定义同名 fixture（会话级），
    会与函数级的 fixture 产生 ScopeMismatch 冲突。这里改用 `server_base_url` 避免冲突。
    """
    return live_server.url


@pytest.fixture
def api(server_base_url) -> str:
    """API 根路径，所有 djoser 端点挂在 /auth/ 下。"""
    return f"{server_base_url}/auth"


@pytest.fixture
def ui_page(ui_seed_data):
    """
    Playwright 页面对象（用例级）。

    **关键约束：浏览器启动后不能再执行任何 ORM 操作。**

    Playwright 的 sync API 会切换到自己的事件循环；一旦浏览器启动，
    Django 检测到当前线程处于异步上下文，随后任何 ORM 调用都会抛
    `SynchronousOnlyOperation: You cannot call this from an async context`。
    （已实测确认：启动前 ORM 正常，启动后必然失败。）

    因此所有测试数据必须在浏览器启动**之前**建好 —— 由 `ui_seed_data` fixture 完成，
    它通过 `ui_page` 的依赖链保证顺序：建数据 → 启动浏览器 → 执行用例。

    每个用例使用**独立的 BrowserContext**，cookie / session 完全隔离，
    避免用例间登录态互相污染。

    浏览器不可用时（未执行 `playwright install chromium`）整个 UI 模块跳过，
    不影响接口测试的正常执行。
    """
    from playwright.sync_api import sync_playwright

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"Playwright 浏览器不可用（请先执行 playwright install chromium）：{exc}"
        )

    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(15000)
    try:
        yield page
    finally:
        context.close()
        browser.close()
        pw.stop()


@pytest.fixture
def ui_seed_data(db):
    """
    在浏览器启动**之前**准备好 UI 测试所需的账号数据。

    由于 Playwright 启动后无法再执行 ORM 操作，所有账号必须在此处一次性建好，
    用例中只能通过 UI 交互（不能直接查库或建用户）。

    提供两个账号：
    - uitester   : 后台管理员（superuser）
    - normaluser : 普通用户（无 admin 权限）
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    User.objects.create_superuser(
        username="uitester", email="uitester@example.com", password="Adm1nPass!23"
    )
    User.objects.create_user(
        username="normaluser", email="normaluser@example.com", password="Us3rPass!23"
    )
    return {
        "admin": {"username": "uitester", "password": "Adm1nPass!23"},
        "normal": {"username": "normaluser", "password": "Us3rPass!23"},
    }


@pytest.fixture(scope="session", autouse=True)
def _stable_stderr():
    """
    pytest-django 的 live_server 用守护线程承载 WSGI 服务。
    当服务线程仍在向 stderr 写入时解释器开始退出，会出现
    "Fatal Python error: _enter_buffered_busy"，导致进程退出码非零。

    该问题会让 CI 判定为失败（尽管用例全部通过），因此在会话结束时
    主动 flush 并等待线程池回收，把竞态窗口关掉。
    """
    yield
    import gc
    import sys

    try:
        sys.stderr.flush()
    except Exception:
        pass
    gc.collect()
