"""
T03 · 密码管理接口测试

被测端点（djoser 2.x 真实路由）：
- POST /auth/users/set_password/              登录态下修改密码
- POST /auth/users/reset_password/             发起密码重置
- POST /auth/users/reset_password_confirm/     校验重置令牌并设置新密码

注意：djoser 2.x 中不存在 `/users/password_reset/` 这样的路径，
密码重置端点的 action 名为 `reset_password` → 实际路由 `/users/reset_password/`。
而用户名重置的 action 名为 `reset_username`，却被显式重写为
`reset_{User.USERNAME_FIELD}`，两套命名规则并不对称。

覆盖维度：正常改密、旧密码校验、弱密码拒绝、令牌有效性、重置链路端到端
"""

import pytest
from django.core import mail

from conftest import VALID_PASSWORD, WEAK_PASSWORD

pytestmark = pytest.mark.django_db


@pytest.mark.smoke
def test_set_password_success(auth_client, api):
    """修改密码应返回 204，且新密码可登录、旧密码失效。"""
    client, user, _ = auth_client
    new_password = "N3wStr0ngPass!45"

    resp = client.post(
        f"{api}/users/set_password/",
        json={"new_password": new_password, "current_password": VALID_PASSWORD},
    )
    assert resp.status_code == 204, f"期望 204，实际 {resp.status_code}: {resp.text}"

    # 旧密码应失效（djoser 对无效凭据返回 400 ValidationError）
    old_login = client.post(
        f"{api}/token/login/", json={"username": user.username, "password": VALID_PASSWORD}
    )
    assert old_login.status_code == 400, "改密后旧密码仍可登录"

    # 新密码应可用
    new_login = client.post(
        f"{api}/token/login/", json={"username": user.username, "password": new_password}
    )
    assert new_login.status_code == 200, "改密后新密码无法登录"


@pytest.mark.negative
def test_set_password_wrong_current_password(auth_client, api):
    """当前密码错误时应拒绝，且密码不得变更。"""
    client, user, _ = auth_client

    resp = client.post(
        f"{api}/users/set_password/",
        json={"new_password": "AnotherPass!987", "current_password": "WrongPass!999"},
    )
    assert resp.status_code == 400

    # 旧密码应仍然有效
    login = client.post(
        f"{api}/token/login/", json={"username": user.username, "password": VALID_PASSWORD}
    )
    assert login.status_code == 200, "密码校验失败却已修改密码"


@pytest.mark.negative
def test_set_password_weak_password_rejected(auth_client, api):
    """弱密码应被密码校验器拒绝。"""
    client, user, _ = auth_client

    resp = client.post(
        f"{api}/users/set_password/",
        json={"new_password": WEAK_PASSWORD, "current_password": VALID_PASSWORD},
    )
    assert resp.status_code == 400
    assert "new_password" in resp.json()


@pytest.mark.negative
def test_set_password_requires_authentication(anon_client, api):
    """未认证改密应返回 401。"""
    resp = anon_client.post(
        f"{api}/users/set_password/",
        json={"new_password": "N3wStr0ngPass!45", "current_password": VALID_PASSWORD},
    )
    assert resp.status_code == 401


@pytest.mark.negative
def test_set_password_missing_fields(auth_client, api):
    """缺少字段应返回 400，不得 500。"""
    client, _, _ = auth_client

    resp = client.post(f"{api}/users/set_password/", json={})
    assert resp.status_code == 400
    assert resp.status_code != 500


@pytest.mark.smoke
def test_password_reset_flow_end_to_end(auth_client, api, create_user):
    """
    密码重置端到端：发起重置 → 取邮件中的 uid/token → 确认重置 → 用新密码登录。
    """
    client, user, _ = auth_client

    # 1) 发起重置
    resp = client.post(f"{api}/users/reset_password/", json={"email": user.email})
    assert resp.status_code == 204, f"期望 204，实际 {resp.status_code}: {resp.text}"

    # 2) 取邮件
    assert len(mail.outbox) == 1, "未收到密码重置邮件"
    mail_body = mail.outbox[0].body
    assert "uid" in mail_body and "token" in mail_body

    # 3) 解析 uid 与 token
    import re
    uid = re.search(r"uid=([A-Za-z0-9_\-]+)", mail_body)
    token = re.search(r"token=([A-Za-z0-9_\-]+)", mail_body)
    assert uid and token, f"邮件中未解析出 uid/token: {mail_body[:400]}"
    uid_val, token_val = uid.group(1), token.group(1)

    # 4) 确认重置
    new_password = "ResetStr0ngPass!77"
    confirm = client.post(
        f"{api}/users/reset_password_confirm/",
        json={
            "uid": uid_val,
            "token": token_val,
            "new_password": new_password,
        },
    )
    assert confirm.status_code == 204, f"期望 204，实际 {confirm.status_code}: {confirm.text}"

    # 5) 新密码可登录
    login = client.post(
        f"{api}/token/login/", json={"username": user.username, "password": new_password}
    )
    assert login.status_code == 200


@pytest.mark.negative
def test_password_reset_confirm_invalid_token_rejected(auth_client, api, create_user):
    """无效的 reset token 应被拒绝，不得 500。"""
    client, user, _ = auth_client

    resp = client.post(
        f"{api}/users/reset_password_confirm/",
        json={
            "uid": "MQ",
            "token": "invalid-token-value",
            "new_password": "ResetStr0ngPass!77",
        },
    )
    assert resp.status_code == 400
    assert resp.status_code != 500


@pytest.mark.negative
def test_password_reset_unknown_email_returns_204(anon_client, api):
    """
    行为观察：重置接口对不存在的邮箱也返回 204（防用户枚举）。

    djoser 提供 PASSWORD_RESET_SHOW_EMAIL_NOT_FOUND 选项控制此行为，
    默认关闭时返回 204 以避免暴露邮箱是否存在。此处固化默认行为。
    """
    resp = anon_client.post(
        f"{api}/users/reset_password/", json={"email": "nonexistent@example.com"}
    )
    assert resp.status_code == 204
