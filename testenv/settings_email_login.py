"""
djoser 非常规配置测试环境。

与 `testenv.settings` 的唯一差异：LOGIN_FIELD 设为 "email"，与 User.USERNAME_FIELD("username") 不一致。

为什么需要独立的 settings 模块？
--------------------------------
djoser 的 `UserCreateSerializer.Meta.fields` 在 **类定义时（模块导入时）**
就把 `settings.LOGIN_FIELD` 求值为静态元组：

    class Meta:
        model = User
        fields = tuple(User.REQUIRED_FIELDS) + (
            settings.LOGIN_FIELD,   # ← 导入时固化
            settings.USER_ID_FIELD,
            "password",
        )

因此运行期修改 `DJOSER` 配置（override_settings）**不会**让该字段重新计算，
只有重启进程（重新导入模块）才会生效。

结论：配置矩阵测试必须用**独立进程**执行，不能在同一个 pytest 进程里切换。
本模块即为该矩阵测试的专用环境。
"""

from testenv.settings import *  # noqa: F401,F403

DJOSER = {
    "LOGIN_FIELD": "email",
    "SEND_ACTIVATION_EMAIL": False,
    "SEND_CONFIRMATION_EMAIL": False,
    "PASSWORD_RESET_CONFIRM_SAME_PASSWORD": False,
    "PASSWORD_RESET_CONFIRM_URL": "https://frontend.example.com/reset-password?uid={uid}&token={token}",
    "USERNAME_RESET_CONFIRM_URL": "https://frontend.example.com/reset-username?uid={uid}&token={token}",
    "ACTIVATION_URL": "https://frontend.example.com/activate?uid={uid}&token={token}",
    "CONFIRMATION_URL": "https://frontend.example.com/confirm?uid={uid}&token={token}",
}
