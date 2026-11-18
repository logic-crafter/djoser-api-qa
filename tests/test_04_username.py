"""
T04 · 用户名管理接口测试（缺陷集中区）

被测端点：
- POST /auth/users/set_username/          登录态下直接改用户名
- POST /auth/users/reset_username/        发起用户名重置（发邮件）
- POST /auth/users/reset_username_confirm/ 校验重置令牌并改名

本模块在 `LOGIN_FIELD` 与 `USERNAME_FIELD` 不一致的配置下会暴露真实缺陷，
相关用例用 xfail 标记（见 test_07_regression.py 的严格回归锁）。
"""

import pytest

from conftest import VALID_PASSWORD

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# 默认配置（LOGIN_FIELD == USERNAME_FIELD == "username"）下的正常行为
# --------------------------------------------------------------------------

@pytest.mark.smoke
def test_set_username_success(auth_client, api, unique_username):
    """默认配置下改用户名应成功，且响应体为空（204）。"""
    client, user, _ = auth_client
    new_name = f"newname_{unique_username}"

    resp = client.post(
        f"{api}/users/set_username/",
        json={"new_username": new_name, "current_password": VALID_PASSWORD},
    )
    assert resp.status_code == 204, f"期望 204，实际 {resp.status_code}: {resp.text}"

    # 验证改名确实落库
    user.refresh_from_db()
    assert user.username == new_name


@pytest.mark.negative
def test_set_username_wrong_current_password(auth_client, api):
    """当前密码错误时应拒绝，且不得改名。"""
    client, user, _ = auth_client
    old_name = user.username

    resp = client.post(
        f"{api}/users/set_username/",
        json={"new_username": "should_not_apply", "current_password": "WrongPass!999"},
    )
    assert resp.status_code == 400

    user.refresh_from_db()
    assert user.username == old_name, "密码校验失败时用户名不应被修改"


@pytest.mark.negative
def test_set_username_duplicate_rejected(auth_client, api, create_user):
    """改成已存在的用户名应被拒绝。"""
    client, user, _ = auth_client
    other, _ = create_user()

    resp = client.post(
        f"{api}/users/set_username/",
        json={"new_username": other.username, "current_password": VALID_PASSWORD},
    )
    assert resp.status_code == 400

    user.refresh_from_db()
    assert user.username != other.username


@pytest.mark.negative
def test_set_username_requires_authentication(anon_client, api):
    """未认证调用改用户名接口应返回 401。"""
    resp = anon_client.post(
        f"{api}/users/set_username/",
        json={"new_username": "someone", "current_password": VALID_PASSWORD},
    )
    assert resp.status_code == 401


@pytest.mark.negative
def test_set_username_missing_fields(auth_client, api):
    """缺少必要字段应返回 400，不得 500。"""
    client, _, _ = auth_client

    resp = client.post(f"{api}/users/set_username/", json={})
    assert resp.status_code == 400
    assert resp.status_code != 500


@pytest.mark.negative
def test_set_username_invalid_type_no_500(auth_client, api):
    """非法类型不得导致 500。"""
    client, _, _ = auth_client

    resp = client.post(
        f"{api}/users/set_username/",
        json={"new_username": ["a", "b"], "current_password": VALID_PASSWORD},
    )
    assert resp.status_code != 500, f"非法类型导致服务端错误: {resp.text}"


@pytest.mark.boundary
def test_set_username_over_max_length_rejected(auth_client, api):
    """超长用户名应被拒绝，而不是截断入库或 500。"""
    client, user, _ = auth_client
    old_name = user.username

    resp = client.post(
        f"{api}/users/set_username/",
        json={"new_username": "a" * 300, "current_password": VALID_PASSWORD},
    )
    assert resp.status_code == 400

    user.refresh_from_db()
    assert user.username == old_name


@pytest.mark.negative
def test_set_username_empty_rejected(auth_client, api):
    """空用户名应被拒绝。"""
    client, user, _ = auth_client
    old_name = user.username

    resp = client.post(
        f"{api}/users/set_username/",
        json={"new_username": "", "current_password": VALID_PASSWORD},
    )
    assert resp.status_code == 400

    user.refresh_from_db()
    assert user.username == old_name
