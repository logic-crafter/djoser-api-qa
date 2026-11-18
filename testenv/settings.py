"""
djoser 测试环境 Django 配置。

设计要点：
- 使用 SQLite + Django LiveServer，保证用例跑在真实 WSGI 栈上（中间件/鉴权/序列化全参与）
- LOGIN_FIELD 默认与 USERNAME_FIELD 一致（生产常见配置），
  另有 settings_email_login.py 覆盖二者不一致的场景（缺陷 BUG-01 / BUG-02 的触发条件）

关于数据库配置
--------------
测试库使用**文件型 SQLite**（而非 `:memory:`），原因：

`:memory:` 数据库在 SQLite 中是**按连接隔离**的 —— 每个连接看到的是各自独立的空库。
而 Django 的 LiveServer 在**独立线程**中处理请求，该线程会建立自己的数据库连接。
结果就是：主线程通过 ORM 创建的用户，LiveServer 线程的连接**看不到**，
表现为"刚创建的用户立刻登录却提示凭据无效"。

改用文件型 SQLite 后，所有线程的连接指向同一个数据库文件，数据天然共享，
既避开了线程隔离问题，也让并发用例（多线程同时注册）成为可能。
"""

import os
import tempfile
from pathlib import Path

SECRET_KEY = "djoser-api-qa-test-secret-key-not-for-production"

DEBUG = False

ALLOWED_HOSTS = ["*"]

# 文件型 SQLite：所有连接共享同一份数据，支持 live_server 跨线程访问。
#
# 文件名带进程号，避免并行场景下多个进程（父进程 + 配置矩阵子进程）
# 指向同一个库文件 —— pytest-django 在 teardown 时会删除测试库，
# 并发删除会导致 "Error when trying to teardown test databases: FileNotFoundError"。
_TEST_DB_PATH = Path(tempfile.gettempdir()) / f"djoser_api_qa_test_{os.getpid()}.sqlite3"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_TEST_DB_PATH),
        "TEST": {"NAME": str(_TEST_DB_PATH)},
    }
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sites",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "djoser",
]

SITE_ID = 1

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "testenv.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Django Admin 后台：作为 UI 自动化测试的被测界面
STATIC_URL = "/static/"
ADMIN_URL = "admin/"

USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 加速：用最快的 hasher，测试不依赖密码哈希强度
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

# djoser 配置：默认 LOGIN_FIELD 与 USERNAME_FIELD 一致。
# 密码重置邮件模板需要这些 URL 模板占位符，由使用方（前端应用）提供，属于必填配置项。
DJOSER = {
    "LOGIN_FIELD": "username",
    "SEND_ACTIVATION_EMAIL": False,
    "SEND_CONFIRMATION_EMAIL": False,
    "PASSWORD_RESET_CONFIRM_SAME_PASSWORD": False,
    "PASSWORD_RESET_CONFIRM_URL": "https://frontend.example.com/reset-password?uid={uid}&token={token}",
    "USERNAME_RESET_CONFIRM_URL": "https://frontend.example.com/reset-username?uid={uid}&token={token}",
    "ACTIVATION_URL": "https://frontend.example.com/activate?uid={uid}&token={token}",
    "CONFIRMATION_URL": "https://frontend.example.com/confirm?uid={uid}&token={token}",
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# 显式开启 Django 密码校验器。
# djoser 的 UserCreateSerializer 会调用 django.contrib.auth.password_validation.validate_password，
# 但只有当 AUTH_PASSWORD_VALIDATORS 非空时才会真正拦截弱密码。
# 开启后，弱密码用例才具备实际测试意义。
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
                "UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation."
                "MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {
        "NAME": "django.contrib.auth.password_validation."
                "CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation."
                "NumericPasswordValidator",
    },
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}
