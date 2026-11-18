"""
T07 · 并发与幂等性测试

被测场景：
- 并发注册同一用户名 → 唯一性约束在并发下是否仍然生效
- 并发改同一账号的用户名 → 是否产生脏写
- 并发登录 → Token 签发是否幂等

价值：并发问题是生产环境"偶发 500 / 数据错乱"的常见根因，
而单线程回归测试完全无法覆盖。本模块用真实 HTTP 并发请求触发。

依赖：testenv/settings.py 使用文件型 SQLite，跨线程共享同一数据库，
因此并发场景可被真实复现。
"""

import concurrent.futures
import uuid

import pytest

pytestmark = pytest.mark.django_db

CONCURRENCY = 8


@pytest.mark.concurrency
def test_concurrent_registration_same_username(anon_client, api):
    """
    并发注册同一用户名：应恰好成功 1 次，其余全部被唯一性约束拒绝。

    关注点：不得出现 500（未捕获的 IntegrityError），也不得出现多次成功。
    """
    username = f"race_{uuid.uuid4().hex[:10]}"
    payload = {
        "username": username,
        "email": f"{username}@example.com",
        "password": "Str0ngPass!23",
    }

    def _post():
        return anon_client.post(f"{api}/users/", json=payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        responses = list(pool.map(lambda _: _post(), range(CONCURRENCY)))

    codes = [r.status_code for r in responses]
    created = codes.count(201)
    rejected = codes.count(400)
    server_errors = [c for c in codes if c >= 500]

    assert not server_errors, f"并发注册出现服务端错误，状态码分布：{codes}"
    assert created == 1, f"并发注册同一用户名应恰好成功 1 次，实际 {created} 次，分布：{codes}"
    assert created + rejected == CONCURRENCY, (
        f"存在非 201/400 的响应，状态码分布：{codes}"
    )


@pytest.mark.concurrency
def test_concurrent_registration_different_usernames(anon_client, api):
    """
    并发注册不同用户名：全部应成功，不应因并发写导致丢失或 500。
    """
    usernames = [f"multi_{uuid.uuid4().hex[:10]}" for _ in range(CONCURRENCY)]

    def _post(name):
        return anon_client.post(
            f"{api}/users/",
            json={
                "username": name,
                "email": f"{name}@example.com",
                "password": "Str0ngPass!23",
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        responses = list(pool.map(_post, usernames))

    codes = [r.status_code for r in responses]
    assert codes.count(201) == CONCURRENCY, (
        f"并发注册不同用户名应全部成功，状态码分布：{codes}"
    )


@pytest.mark.concurrency
def test_concurrent_login_token_idempotent(anon_client, api, registered_user):
    """
    并发登录同一账号：Token 应保持稳定（幂等），不应签发多个不同 Token。

    djoser 的 login_user 使用 get_or_create 语义，理论上应返回同一个 Token。
    若返回多个不同 Token，说明存在竞态，会导致登出后其他 Token 仍然可用。
    """
    def _login():
        return anon_client.post(
            f"{api}/token/login/",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        responses = list(pool.map(lambda _: _login(), range(CONCURRENCY)))

    codes = [r.status_code for r in responses]
    assert all(c == 200 for c in codes), f"并发登录存在失败响应，状态码分布：{codes}"

    tokens = {r.json()["auth_token"] for r in responses}
    assert len(tokens) == 1, f"并发登录应签发唯一 Token，实际签发了 {len(tokens)} 个"


@pytest.mark.concurrency
def test_concurrent_set_username_keeps_single_winner(auth_client, api):
    """
    并发对同一账号发起多个改名请求：最终用户名应只取其中一个值，
    不应出现数据错乱或 500。

    关注点：允许部分请求失败（唯一性冲突），但不得出现服务端错误。
    """
    client, user, _ = auth_client
    token_header = client.headers.get("Authorization")

    candidates = [f"race_name_{uuid.uuid4().hex[:8]}" for _ in range(CONCURRENCY)]

    def _rename(name):
        return client.post(
            f"{api}/users/set_username/",
            json={"new_username": name, "current_password": "Str0ngPass!23"},
            headers={"Authorization": token_header},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        responses = list(pool.map(_rename, candidates))

    codes = [r.status_code for r in responses]
    server_errors = [c for c in codes if c >= 500]
    assert not server_errors, f"并发改名出现服务端错误，状态码分布：{codes}"

    # 至少应有一个成功
    assert codes.count(204) >= 1, f"并发改名无一成功，状态码分布：{codes}"

    # 最终状态必须与某个成功请求的目标一致（不得出现半写入）
    user.refresh_from_db()
    assert user.username in candidates, (
        f"并发改名后用户名为 {user.username}，不在候选集合中，出现脏写"
    )
