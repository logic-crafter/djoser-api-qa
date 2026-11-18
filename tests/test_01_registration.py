"""
T01 · 用户注册接口测试

被测端点：POST /auth/users/
覆盖维度：正常流程、唯一性约束、必填缺失、类型错误、边界长度、契约一致性、SQL 注入尝试
"""

import pytest

from conftest import VALID_PASSWORD, WEAK_PASSWORD

pytestmark = pytest.mark.django_db


@pytest.mark.smoke
def test_register_success(anon_client, api, unique_username):
    """正常注册应返回 201，并回显 username/email。"""
    payload = {
        "username": unique_username,
        "password": VALID_PASSWORD,
        "email": f"{unique_username}@example.com",
    }
    resp = anon_client.post(f"{api}/users/", json=payload)

    assert resp.status_code == 201, f"期望 201，实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["username"] == unique_username
    assert body["email"] == payload["email"]
    # 密码绝不能出现在响应体中
    assert VALID_PASSWORD not in resp.text


@pytest.mark.smoke
def test_register_duplicate_username_rejected(anon_client, api, registered_user):
    """用户名唯一性约束：重复注册应返回 400。"""
    resp = anon_client.post(
        f"{api}/users/",
        json={
            "username": registered_user["username"],
            "password": VALID_PASSWORD,
            "email": "another@example.com",
        },
    )
    assert resp.status_code == 400
    assert "username" in resp.json()


@pytest.mark.negative
def test_register_missing_required_fields(anon_client, api):
    """必填字段缺失应返回 400 且给出字段级错误。"""
    resp = anon_client.post(f"{api}/users/", json={})

    assert resp.status_code == 400
    errors = resp.json()
    assert "username" in errors
    assert "password" in errors


@pytest.mark.negative
@pytest.mark.parametrize(
    "payload",
    [
        {"username": None, "password": VALID_PASSWORD},
        {"username": "", "password": VALID_PASSWORD},
        {"username": ["a", "b"], "password": VALID_PASSWORD},
        {"username": "okuser", "password": None},
        {"username": "okuser", "password": ""},
        {"username": "okuser", "password": ["a", "b"]},
    ],
    ids=["username-null", "username-empty", "username-list",
         "password-null", "password-empty", "password-list"],
)
def test_register_invalid_types_rejected(anon_client, api, payload):
    """非法类型输入应被参数校验拦截，不得 500。"""
    resp = anon_client.post(f"{api}/users/", json=payload)

    assert resp.status_code == 400, f"非法类型不应导致服务端错误，实际 {resp.status_code}"
    assert resp.status_code != 500


@pytest.mark.negative
@pytest.mark.parametrize("numeric_value", [12345, 123456])
def test_register_numeric_username_coerced_to_string(
    anon_client, api, numeric_value
):
    """
    观察性用例（行为记录，非缺陷）：DRF 的 CharField 会把 int 隐式转为 str。

    `username=12345` 会被接受并注册为字符串用户名 "12345"，返回 201。
    这不是崩溃，而是框架的类型强制转换行为。

    该行为对业务方有潜在风险：前端若误传数字类型，用户名会变成"看起来像 ID"的值。
    本用例固化当前行为，防止上游无意变更时能第一时间感知。
    """
    resp = anon_client.post(
        f"{api}/users/",
        json={
            "username": numeric_value,
            "password": VALID_PASSWORD,
            "email": "numeric@example.com",
        },
    )
    assert resp.status_code == 201
    # 响应中的 username 应为字符串形式的数字
    assert resp.json()["username"] == str(numeric_value)
    assert isinstance(resp.json()["username"], str)


@pytest.mark.boundary
def test_register_username_over_max_length_rejected(anon_client, api):
    """用户名超长应被拒绝，而不是截断后入库。"""
    resp = anon_client.post(
        f"{api}/users/",
        json={
            "username": "a" * 300,
            "password": VALID_PASSWORD,
            "email": "long@example.com",
        },
    )
    assert resp.status_code == 400


@pytest.mark.negative
def test_register_weak_password_rejected(anon_client, api, unique_username):
    """
    弱密码应被密码强度校验拒绝。

    前置条件：testenv/settings.py 中显式配置了 AUTH_PASSWORD_VALIDATORS。
    djoser 的 UserCreateSerializer 会调用 validate_password，但仅当校验器列表非空时才生效。
    """
    resp = anon_client.post(
        f"{api}/users/",
        json={"username": unique_username, "password": WEAK_PASSWORD},
    )
    assert resp.status_code == 400, f"弱密码应被拒绝，实际 {resp.status_code}: {resp.text}"
    assert "password" in resp.json()


@pytest.mark.negative
def test_register_sql_injection_payload_not_executed(anon_client, api):
    """SQL 注入载荷应被当作普通字符串处理，不得出现 500。"""
    malicious = "admin' OR '1'='1"
    resp = anon_client.post(
        f"{api}/users/",
        json={
            "username": malicious,
            "password": VALID_PASSWORD,
            "email": "inj@example.com",
        },
    )
    # 无论注册成功还是被拒绝，都不允许服务端崩溃
    assert resp.status_code != 500, f"注入载荷导致服务端错误: {resp.text}"
    assert resp.status_code in (201, 400)


@pytest.mark.contract
def test_register_response_contract(anon_client, api, unique_username):
    """注册响应契约：字段集合与类型应稳定。"""
    resp = anon_client.post(
        f"{api}/users/",
        json={
            "username": unique_username,
            "password": VALID_PASSWORD,
            "email": f"{unique_username}@example.com",
        },
    )
    assert resp.status_code == 201
    body = resp.json()

    for field in ("username", "email"):
        assert field in body, f"响应缺少字段 {field}"
        assert isinstance(body[field], str)
