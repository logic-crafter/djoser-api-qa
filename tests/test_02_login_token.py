"""
T02 · 登录与 Token 管理接口测试

被测端点：
- POST /auth/token/login/    （注意：不是 /auth/users/token/，后者不是有效路径）
- POST /auth/token/logout/

覆盖维度：正常登录、错误口令、错误用户名、缺参、类型错误、Token 签发契约、
登出后 Token 失效、Token 越权访问
"""

import pytest

from conftest import VALID_PASSWORD

pytestmark = pytest.mark.django_db


@pytest.mark.smoke
def test_login_success(anon_client, api, registered_user):
    """正常登录应返回 200 并签发 auth_token。"""
    resp = anon_client.post(
        f"{api}/token/login/",
        json={"username": registered_user["username"], "password": registered_user["password"]},
    )
    assert resp.status_code == 200, f"期望 200，实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "auth_token" in body
    assert body["auth_token"]
    assert len(body["auth_token"]) >= 20


@pytest.mark.negative
def test_login_wrong_password_rejected(anon_client, api, registered_user):
    """
    错误口令应被拒绝。

    djoser 的 TokenCreateSerializer 在凭据无效时抛 DRF ValidationError
    （常量 INVALID_CREDENTIALS_ERROR: "Unable to log in with provided credentials."），
    因此状态码为 400 而非 401。此处按框架既定行为断言。
    """
    resp = anon_client.post(
        f"{api}/token/login/",
        json={"username": registered_user["username"], "password": "WrongPass!999"},
    )
    assert resp.status_code == 400, f"期望 400，实际 {resp.status_code}: {resp.text}"
    # 错误信息不应包含用户是否存在等敏感信息
    assert "auth_token" not in resp.text
    assert "Unable to log in with provided credentials." in resp.text


@pytest.mark.negative
def test_login_unknown_user_rejected(anon_client, api):
    """不存在的用户应被拒绝，且与错误口令返回同样的信息（防用户枚举）。"""
    resp = anon_client.post(
        f"{api}/token/login/",
        json={"username": "no_such_user_zzz", "password": VALID_PASSWORD},
    )
    assert resp.status_code == 400
    assert "Unable to log in with provided credentials." in resp.text


@pytest.mark.negative
def test_login_unknown_user_same_message_as_wrong_password(
    anon_client, api, registered_user
):
    """
    防用户枚举：不存在的用户 与 错误口令 必须返回完全一致的响应，
    否则攻击者可据此批量探测有效账号。
    """
    unknown = anon_client.post(
        f"{api}/token/login/",
        json={"username": "no_such_user_zzz", "password": VALID_PASSWORD},
    )
    wrong = anon_client.post(
        f"{api}/token/login/",
        json={"username": registered_user["username"], "password": "WrongPass!999"},
    )
    assert unknown.status_code == wrong.status_code
    assert unknown.json() == wrong.json(), "响应不一致，存在用户枚举风险"


@pytest.mark.negative
def test_login_missing_password_rejected(anon_client, api, registered_user):
    """缺少密码应返回 400。"""
    resp = anon_client.post(
        f"{api}/token/login/", json={"username": registered_user["username"]}
    )
    assert resp.status_code == 400


@pytest.mark.negative
def test_login_invalid_type_rejected(anon_client, api):
    """非法的凭据类型不应导致 500。"""
    resp = anon_client.post(
        f"{api}/token/login/", json={"username": ["a"], "password": {"x": 1}}
    )
    assert resp.status_code != 500, f"非法类型导致服务端错误: {resp.text}"
    assert resp.status_code in (400, 401)


@pytest.mark.contract
def test_login_response_contract(anon_client, api, registered_user):
    """登录响应契约：字段名与类型应稳定。"""
    resp = anon_client.post(
        f"{api}/token/login/",
        json={"username": registered_user["username"], "password": registered_user["password"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert isinstance(body["auth_token"], str)
    # 响应中不应出现密码
    assert registered_user["password"] not in resp.text


@pytest.mark.smoke
def test_logout_invalidates_token(anon_client, api, create_user):
    """登出后原 Token 必须失效——这是认证生命周期的核心断言。"""
    user, token_key = create_user()

    # 登出前，Token 可用
    me = anon_client.get(
        f"{api}/users/me/", headers={"Authorization": f"Token {token_key}"}
    )
    assert me.status_code == 200

    # 执行登出
    out = anon_client.post(
        f"{api}/token/logout/", headers={"Authorization": f"Token {token_key}"}
    )
    assert out.status_code == 204

    # 登出后，同一 Token 必须失效
    after = anon_client.get(
        f"{api}/users/me/", headers={"Authorization": f"Token {token_key}"}
    )
    assert after.status_code == 401, "登出后 Token 仍可用，认证生命周期存在漏洞"


@pytest.mark.negative
def test_me_requires_authentication(anon_client, api):
    """未认证访问 /users/me/ 应返回 401。"""
    resp = anon_client.get(f"{api}/users/me/")
    assert resp.status_code == 401


@pytest.mark.negative
def test_me_rejects_invalid_token(anon_client, api):
    """伪造的 Token 必须被拒绝。"""
    resp = anon_client.get(
        f"{api}/users/me/", headers={"Authorization": "Token 0000000000000000000000000000000000000000"}
    )
    assert resp.status_code == 401


@pytest.mark.smoke
def test_me_returns_current_user(auth_client, api):
    """认证态下 /users/me/ 应返回当前用户信息。"""
    client, user, _ = auth_client
    resp = client.get(f"{api}/users/me/")

    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == user.username
    assert "password" not in body
