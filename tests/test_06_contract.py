"""
T06 · 接口契约与响应 Schema 一致性测试

用 JSON Schema 描述各端点的响应结构，校验实际响应是否符合契约。

价值：契约测试能在上游重构、字段增删时第一时间发现**破坏性变更**，
这类变更往往不会被业务用例捕获，但会直接打穿前端。

覆盖维度：字段存在性、字段类型、必填性、枚举取值、响应中不得出现的敏感字段
"""

import pytest
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.django_db

# --------------------------------------------------------------------------
# 契约定义
# --------------------------------------------------------------------------

USER_SCHEMA = {
    "type": "object",
    "required": ["id", "username", "email"],
    "properties": {
        "id": {"type": "integer"},
        "username": {"type": "string"},
        "email": {"type": "string", "format": "email"},
    },
}

TOKEN_SCHEMA = {
    "type": "object",
    "required": ["auth_token"],
    "properties": {
        "auth_token": {"type": "string", "minLength": 20},
    },
}

ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "detail": {"type": "string"},
        "non_field_errors": {"type": "array", "items": {"type": "string"}},
    },
}


def _assert_valid(instance, schema, label):
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    assert not errors, (
        f"{label} 响应不符合契约：\n"
        + "\n".join(f"  - {list(e.path)}: {e.message}" for e in errors)
        + f"\n实际响应：{instance}"
    )


@pytest.mark.contract
def test_register_response_matches_schema(anon_client, api, unique_username):
    """注册响应应符合用户对象契约。"""
    resp = anon_client.post(
        f"{api}/users/",
        json={
            "username": unique_username,
            "email": f"{unique_username}@example.com",
            "password": "Str0ngPass!23",
        },
    )
    assert resp.status_code == 201
    _assert_valid(resp.json(), USER_SCHEMA, "POST /auth/users/")


@pytest.mark.contract
def test_login_response_matches_schema(anon_client, api, registered_user):
    """登录响应应符合 Token 契约。"""
    resp = anon_client.post(
        f"{api}/token/login/",
        json={
            "username": registered_user["username"],
            "password": registered_user["password"],
        },
    )
    assert resp.status_code == 200
    _assert_valid(resp.json(), TOKEN_SCHEMA, "POST /auth/token/login/")


@pytest.mark.contract
def test_me_response_matches_schema(auth_client, api):
    """当前用户响应应符合用户对象契约。"""
    client, _, _ = auth_client
    resp = client.get(f"{api}/users/me/")

    assert resp.status_code == 200
    _assert_valid(resp.json(), USER_SCHEMA, "GET /auth/users/me/")


@pytest.mark.contract
def test_error_response_matches_schema(anon_client, api):
    """错误响应应符合错误契约（detail 或 non_field_errors 之一）。"""
    resp = anon_client.post(
        f"{api}/token/login/",
        json={"username": "nobody_here", "password": "Str0ngPass!23"},
    )
    assert resp.status_code == 400
    body = resp.json()
    _assert_valid(body, ERROR_SCHEMA, "POST /auth/token/login/ (错误场景)")
    assert "detail" in body or "non_field_errors" in body, (
        f"错误响应既无 detail 也无 non_field_errors：{body}"
    )


@pytest.mark.contract
def test_user_response_never_exposes_password(auth_client, api):
    """契约红线：任何用户相关响应都不得包含密码字段。"""
    client, user, _ = auth_client

    for path in (f"{api}/users/me/",):
        resp = client.get(path)
        assert resp.status_code == 200
        body = resp.json()
        assert "password" not in body, f"{path} 响应泄露了 password 字段"
        # 同时确认密码哈希没有以任何形式出现
        assert "pbkdf2" not in resp.text.lower()
        assert "md5" not in resp.text.lower()


@pytest.mark.contract
def test_error_responses_are_json_not_html(anon_client, api):
    """契约红线：错误响应必须是 JSON，不能是 HTML 错误页。"""
    resp = anon_client.post(f"{api}/users/", json={"garbage": True})

    assert resp.status_code == 400
    assert resp.headers.get("Content-Type", "").startswith("application/json"), (
        f"错误响应 Content-Type 不是 JSON：{resp.headers.get('Content-Type')}"
    )
    # 不应包含 Django 调试页或 HTML 标签
    assert "<html" not in resp.text.lower()
    assert "traceback" not in resp.text.lower()
