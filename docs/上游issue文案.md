# 上游 issue 文案（供自行发布）

> 本文件含两段文案：
> - **文案 A**：给 [sunscrapers/djoser#922](https://github.com/sunscrapers/djoser/issues/922) 追加评论
> - **文案 B**：为 BUG-02（注册接口 500）新开 issue
>
> 发布方式：打开对应页面 → `New comment` / `New issue` → 粘贴对应文案 → 提交。
>
> 发布后建议回到本项目仓库的 README，把「上游贡献」章节的链接补上（当前写的是 #922）。

---

## 文案 A · 追加到 issue #922

**目标页面**：https://github.com/sunscrapers/djoser/issues/922
**操作**：页面底部 `New comment` → 粘贴以下内容 → Submit comment

---

I confirmed this with an actual runtime reproduction (HTTP requests against a live
Django server), not just static analysis. Two additional data points that may help
with the fix.

### 1. The bug reproduces on the current `master`

Setting `DJOSER = {"LOGIN_FIELD": "email"}` with the default Django `User` model
(`USERNAME_FIELD = "username"`) and calling the endpoint:

```
POST /auth/users/set_username/
Authorization: Token <token>
{"new_email": "changed@example.com", "current_password": "..."}
```

returns **HTTP 500** with an unhandled exception:

```
File "djoser/views.py", line 269, in set_username
    new_username = serializer.data["new_" + User.USERNAME_FIELD]
KeyError: 'new_username'
```

### 2. The client-side field name is not the cause

Passing `new_username` (the key the view actually reads) instead of `new_email`
gives the same 500, so this is not a client misuse — the two layers genuinely
disagree:

```python
# djoser/serializers.py:259-263  — serializer uses settings.LOGIN_FIELD
self.username_field = settings.LOGIN_FIELD
self.fields[f"new_{self.username_field}"] = self.fields.pop(self.username_field)
# → exposes "new_email" when LOGIN_FIELD == "email"

# djoser/views.py:269 and :296  — view uses User.USERNAME_FIELD
new_username = serializer.data["new_" + User.USERNAME_FIELD]
# → looks up "new_username"  →  KeyError
```

### 3. `UsernameSerializer.save()` already contains the compatibility shim, but the views never call it

```python
# djoser/serializers.py:265-270
def save(self, **kwargs):
    if self.username_field != self._default_username_field:
        kwargs[User.USERNAME_FIELD] = self.validated_data.get(f"new_{self.username_field}")
    return super().save(**kwargs)
```

`set_username` and `reset_username_confirm` bypass this by reading `serializer.data`
directly and calling `setattr` + `user.save()` manually, so the shim never runs.

### 4. A possible fix

Deriving the field name from `settings.LOGIN_FIELD` in the views, the same way the
serializer does:

```python
new_username = serializer.data[f"new_{settings.LOGIN_FIELD}"]
```

Alternatively, routing the write through `serializer.save()` would reuse the existing
shim as-is.

### 5. A related finding — registration breaks under the same configuration

While testing this configuration I found that `POST /auth/users/` fails as well,
with a different exception:

```
File "djoser/serializers.py", line 48, in perform_create
    user = User.objects.create_user(**validated_data)
TypeError: UserManager.create_user() missing 1 required positional argument: 'username'
```

`UserCreateSerializer.Meta.fields` computes `tuple(User.REQUIRED_FIELDS) + (settings.LOGIN_FIELD, ...)`,
so with `LOGIN_FIELD == "email"` the `username` field is not part of the writable
fields at all, while Django's default `User` model still requires it.

I have opened a separate issue for that one with the full details.

Happy to open a PR with the fix and a regression test if that would help.

---

## 文案 B · 新开 issue（BUG-02）

**目标页面**：https://github.com/sunscrapers/djoser/issues/new
**建议标题**：

```
User registration fails with TypeError when LOGIN_FIELD differs from USERNAME_FIELD
```

**操作**：`New issue` → 粘贴以下内容 → Submit new issue

---

### Summary

With `DJOSER = {"LOGIN_FIELD": "email"}` and Django's default `User` model
(`USERNAME_FIELD = "username"`), `POST /auth/users/` raises an unhandled
`TypeError` and returns **HTTP 500**. Registration is completely unavailable
under this configuration.

This is related to #922 — that issue covers `set_username` /
`reset_username_confirm`, this one covers registration. Both stem from
`LOGIN_FIELD` and `USERNAME_FIELD` being treated as interchangeable when they
are not.

### Reproduction

Settings:

```python
DJOSER = {"LOGIN_FIELD": "email"}
```

Django default user model (`AUTH_USER_MODEL` not customised, so
`User.USERNAME_FIELD == "username"`).

Then:

```
POST /auth/users/
Content-Type: application/json

{
  "username": "newuser",
  "email": "newuser@example.com",
  "password": "Str0ngPass!23"
}
```

### Actual behaviour

HTTP 500, with the following unhandled exception:

```
File "djoser/views.py", line 134, in perform_create
    user = serializer.save(*args, **kwargs)
  ...
File "djoser/serializers.py", line 48, in perform_create
    user = User.objects.create_user(**validated_data)
TypeError: UserManager.create_user() missing 1 required positional argument: 'username'
```

### Expected behaviour

Either:

- the registration succeeds (with `username` included among the writable fields), or
- the unsupported configuration is rejected early with a clear error
  (a `400`, or an `ImproperlyConfigured` at startup) rather than a `500` at runtime.

### Root cause

`UserCreateSerializer` computes its writable fields from `LOGIN_FIELD`:

```python
# djoser/serializers.py:62-68
class Meta:
    model = User
    fields = tuple(User.REQUIRED_FIELDS) + (
        settings.LOGIN_FIELD,
        settings.USER_ID_FIELD,
        "password",
    )
```

With Django's default `User` model, `REQUIRED_FIELDS == ("email",)` and
`USERNAME_FIELD == "username"`. When `LOGIN_FIELD == "email"`, `username` is
therefore **not** among the writable fields, so `validated_data` never contains it —
but `UserManager.create_user()` still requires it as a positional argument.

A related observation: because `Meta.fields` is evaluated at class-definition
time (i.e. at import), changing `DJOSER` at runtime does not recompute it.
`LOGIN_FIELD` therefore has to be fixed before the module is imported.

### Impact

Any deployment using `LOGIN_FIELD` different from `USERNAME_FIELD` — a common
setup when a project wants email-based login while keeping `username` as the
identifier — cannot register users at all.

This also makes the whole `LOGIN_FIELD != USERNAME_FIELD` combination unusable:

| Flow | Result |
| --- | --- |
| `POST /users/` | HTTP 500 (this issue) |
| `POST /users/set_username/` | HTTP 500 (#922) |
| `POST /token/login/` with email | HTTP 400 — `TokenCreateSerializer` passes the email value as `User.USERNAME_FIELD`, so `authenticate()` matches no user |

### Suggested direction

Including `User.USERNAME_FIELD` among the writable fields for registration:

```python
class Meta:
    model = User
    fields = tuple(User.REQUIRED_FIELDS) + (
        User.USERNAME_FIELD,        # always present
        settings.LOGIN_FIELD,
        settings.USER_ID_FIELD,
        "password",
    )
```

Alternatively — and possibly more helpful for users — validating the configuration
at startup so that an unsupported combination fails fast with a clear message
instead of producing `500`s at runtime:

```python
if settings.LOGIN_FIELD != User.USERNAME_FIELD and settings.LOGIN_FIELD not in User.REQUIRED_FIELDS:
    raise ImproperlyConfigured(
        "DJOSER['LOGIN_FIELD'] must be either User.USERNAME_FIELD or one of User.REQUIRED_FIELDS"
    )
```

I can prepare a PR with a regression test for either approach if that is useful.

---
---

## 发布后的小 checklist

- [ ] 文案 A 已发到 issue #922
- [ ] 文案 B 已新开 issue
- [ ] 回到自己的仓库 `https://github.com/logic-crafter/djoser-api-qa`，
      把 README「七、上游贡献」章节的链接补上新 issue 的 URL
- [ ] 面试时可以直接打开这两个链接，验证你说"我复现了上游 bug"是真的
