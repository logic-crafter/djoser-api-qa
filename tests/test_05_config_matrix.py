"""
T05 · 配置矩阵测试（本项目缺陷发现的核心手段）

背景
----
djoser 的 `LOGIN_FIELD` 与 `User.USERNAME_FIELD` 是两个独立配置。
当二者不一致时（典型场景：用户模型用 username 作为唯一标识，但希望用邮箱登录），
框架内部的序列化器层与视图层使用了不同的字段名，导致未捕获异常。

为什么必须用子进程
------------------
`UserCreateSerializer.Meta.fields` 在**类定义时（模块导入时）**就把
`settings.LOGIN_FIELD` 求值为静态元组：

    class Meta:
        model = User
        fields = tuple(User.REQUIRED_FIELDS) + (
            settings.LOGIN_FIELD,   # ← 导入时固化，运行时改配置无效
            settings.USER_ID_FIELD,
            "password",
        )

因此运行期 `override_settings(DJOSER=...)` **不会**让该字段重新计算，
只有重启进程（重新导入模块）才会生效。

结论：配置矩阵测试必须以独立进程执行。本模块用 subprocess 启动
`DJANGO_SETTINGS_MODULE=testenv.settings_email_login` 的 pytest 子进程，
子进程内的用例通过环境变量 `EXPECT_BUG_*` 与父进程通信，报告真实观测结果。

这种做法本身就是本项目的一个测试方法论结论：**配置类缺陷必须在进程边界上验证。**
"""

import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 子进程观测结果的传递通道
CHILD_RESULT_ENV = "DJOSER_QA_CHILD_RESULT"


def _run_in_child(settings_module: str) -> dict:
    """
    在独立子进程中运行配置探针，收集其观测结果。

    返回 {"status_code": int|None, "output": str, "passed": bool}
    """
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = settings_module
    # CHILD_RESULT_ENV 保持父进程写入的结果文件路径，不得覆盖
    env["PYTHONPATH"] = PROJECT_ROOT
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_05_config_matrix.py::test_child_probe",
            "-v",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "status_code": proc.returncode,
        "output": output,
        "passed": proc.returncode == 0,
    }


# ==========================================================================
# 子进程探针：这个用例由上面的 _run_in_child 在指定配置下执行
# ==========================================================================

@pytest.mark.django_db
def test_child_probe(live_server, create_user):
    """
    子进程探针：在当前 DJANGO_SETTINGS_MODULE 指定的配置下，探测各端点的真实行为。

    把观测结果写入环境变量指定的文件，供父进程断言。
    该用例本身不做断言，断言由父进程完成。

    执行顺序很关键：登录探测必须在改名探测之前，
    否则 set_username 真实修改了用户名，后续用旧用户名登录必然失败，
    导致探针结果自我污染、掩盖真实行为。
    """
    import requests

    api = f"{live_server.url}/auth"
    user, token_key = create_user()
    headers = {"Authorization": f"Token {token_key}"}

    observations = {}

    # 记录账号的实际状态，便于父进程定位观测偏差
    observations["_user_username"] = user.username
    observations["_user_password_ok"] = user.check_password("Str0ngPass!23")
    observations["_user_email"] = user.email

    # 注册
    try:
        r = requests.post(
            f"{api}/users/",
            json={
                "username": "probe_user",
                "email": "probe_user@example.com",
                "password": "Str0ngPass!23",
            },
            timeout=15,
        )
        observations["register"] = r.status_code
    except Exception as exc:  # 服务端 500 导致连接被重置等
        observations["register"] = f"exception:{type(exc).__name__}"

    # 登录：字段名同样随 LOGIN_FIELD 变化，两种都尝试。
    # 注意必须在 set_username 探测**之前**执行 —— set_username 会真实修改用户名，
    # 若放在其后，用旧用户名登录必然失败，造成探针结果自我污染。
    for field_name, value in (
        ("email", user.email),
        ("username", user.username),
    ):
        try:
            r = requests.post(
                f"{api}/token/login/",
                json={field_name: value, "password": "Str0ngPass!23"},
                timeout=15,
            )
            observations[f"login::{field_name}"] = r.status_code
            observations[f"login::{field_name}__body"] = r.text[:200]
            observations[f"login::{field_name}__user"] = value
        except Exception as exc:
            observations[f"login::{field_name}"] = f"exception:{type(exc).__name__}"

    # set_username：字段名随 LOGIN_FIELD 变化 —— 默认配置用 "new_username"，
    # LOGIN_FIELD="email" 配置下序列化器期望 "new_email"。
    # 且字段值必须合法（email 字段需通过 EmailValidator），否则会在序列化阶段
    # 就被 400 拦截，根本走不到出问题的视图层。
    # 这里两种都尝试，记录各自的结果，用于观察字段契约差异。
    probe_values = {
        "new_email": "changed_target@example.com",
        "new_username": "changed_target",
    }
    for field_name, value in probe_values.items():
        # 每个字段名用独立账号，避免改名操作相互干扰
        probe_user, probe_token = create_user()
        probe_headers = {"Authorization": f"Token {probe_token}"}
        try:
            r = requests.post(
                f"{api}/users/set_username/",
                json={field_name: value, "current_password": "Str0ngPass!23"},
                headers=probe_headers,
                timeout=15,
            )
            observations[f"set_username::{field_name}"] = r.status_code
        except Exception as exc:
            observations[f"set_username::{field_name}"] = f"exception:{type(exc).__name__}"

    out_path = os.environ.get(CHILD_RESULT_ENV)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(observations, fh)


# ==========================================================================
# 父进程用例：分别在不同配置下启动子进程，断言观测结果
# ==========================================================================

@pytest.mark.slow
def test_matrix_default_config_behaviour(tmp_path):
    """
    对照组：默认配置（LOGIN_FIELD == USERNAME_FIELD == "username"）
    下，注册 / 改用户名 / 邮箱登录均应正常工作。
    """
    out = tmp_path / "default.json"
    os.environ[CHILD_RESULT_ENV] = str(out)

    result = _run_in_child("testenv.settings")

    assert out.exists(), (
        f"子进程未产出观测结果，可能用例被跳过或崩溃：\n{result['output'][-2000:]}"
    )
    obs = json.loads(out.read_text(encoding="utf-8"))

    assert obs["register"] == 201, f"默认配置下注册应成功，实际 {obs['register']}"
    assert obs["set_username::new_username"] == 204, (
        f"默认配置下 set_username(new_username) 应成功，实际 {obs['set_username::new_username']}"
    )
    assert obs["login::username"] == 200, (
        f"默认配置下用 username 登录应成功，实际 {obs['login::username']}"
    )


@pytest.mark.slow
def test_matrix_email_login_config_exposes_500s(tmp_path):
    """
    BUG-01 / BUG-02：LOGIN_FIELD="email" 配置下，注册与改用户名接口均返回 HTTP 500。

    这是本项目最核心的缺陷证据：在**真实进程边界**上复现，
    排除了同进程配置污染、缓存、ORM 状态等因素的干扰。

    对应上游 issue：sunscrapers/djoser#922（原作者自述未实际执行）
    """
    out = tmp_path / "email_login.json"
    os.environ[CHILD_RESULT_ENV] = str(out)

    result = _run_in_child("testenv.settings_email_login")

    assert out.exists(), (
        f"子进程未产出观测结果：\n{result['output'][-2000:]}"
    )
    obs = json.loads(out.read_text(encoding="utf-8"))

    # BUG-02：注册接口 500
    assert obs["register"] == 500, (
        f"预期复现注册接口 500（BUG-02），实际 {obs['register']}。"
        f"若上游已修复，本用例需相应调整。\n子进程输出：\n{result['output'][-1500:]}"
    )

    # BUG-01：改用户名接口 500 —— 无论客户端传 new_email 还是 new_username，
    # 服务端都在视图层取 "new_username" 时抛 KeyError，因此两种传参都是 500。
    assert obs["set_username::new_email"] == 500, (
        f"预期复现 set_username 接口 500（BUG-01），实际 {obs['set_username::new_email']}。"
        f"若上游已修复，本用例需相应调整。\n子进程输出：\n{result['output'][-1500:]}"
    )
    assert obs["set_username::new_username"] == 500, (
        f"预期复现 set_username 接口 500（BUG-01），实际 {obs['set_username::new_username']}。"
        f"若上游已修复，本用例需相应调整。\n子进程输出：\n{result['output'][-1500:]}"
    )


@pytest.mark.slow
def test_matrix_email_login_config_login_behaviour(tmp_path):
    """
    LOGIN_FIELD="email" 配置下，用邮箱登录的实际行为记录。

    djoser 的 TokenCreateSerializer 用 `params = {User.USERNAME_FIELD: attrs.get(settings.LOGIN_FIELD)}`
    即把 email 值塞进 username 参数去 authenticate。
    由于 Django 默认 User 模型的 USERNAME_FIELD 是 "username"，
    该查询匹配不到任何用户 → 登录必然失败。

    这说明该配置组合下整套认证流程不可用，与 BUG-01/BUG-02 相互印证。
    """
    out = tmp_path / "email_login2.json"
    os.environ[CHILD_RESULT_ENV] = str(out)

    result = _run_in_child("testenv.settings_email_login")

    assert out.exists(), f"子进程未产出观测结果：\n{result['output'][-2000:]}"
    obs = json.loads(out.read_text(encoding="utf-8"))

    assert obs["login::email"] == 400, (
        f"LOGIN_FIELD=email 配置下邮箱登录应失败（400），实际 {obs['login::email']}"
    )
    assert obs["login::username"] == 400, (
        f"LOGIN_FIELD=email 配置下用 username 登录也应失败（400），"
        f"实际 {obs['login::username']}"
    )
