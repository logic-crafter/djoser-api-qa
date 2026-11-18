# djoser-api-qa —— djoser 开源项目接口自动化测试与质量评估

> 对 Django REST 认证框架 [djoser](https://github.com/sunscrapers/djoser) 展开的系统性接口自动化测试，
> 采用黑盒 HTTP 测试 + 白盒源码定位的组合策略，在真实运行环境中复现并定位上游缺陷。

## 一、项目背景

`djoser` 是 Sunscrapers 维护的 Django REST 认证框架，提供注册、登录、Token 签发、密码重置、
用户名变更等完整的账号生命周期接口，被广泛用于 Django 项目的认证层。

本项目不以"练习 pytest"为目的，而是回答三个工程问题：

1. **在真实配置下**（如 `LOGIN_FIELD` 与 `USERNAME_FIELD` 不一致），djoser 的账号管理接口是否正确降级？
2. 该框架的接口在**参数边界、并发写入、契约一致性**上是否存在可观测缺陷？
3. 缺陷能否**被稳定复现**，并定位到具体源码行？

## 二、被测对象与环境

| 项 | 值 |
| --- | --- |
| 被测项目 | sunscrapers/djoser（2.6k star，持续维护中） |
| 被测版本 | djoser 2.3.x / Django 5.2.19 / DRF 3.x |
| 被测接口数 | 12（注册 / 登录 / Token / 当前用户 / 密码 / 用户名） |
| 测试方式 | 黑盒 HTTP（`requests` + Django LiveServer）+ 白盒源码定位 |
| 运行框架 | pytest 9.x + pytest-django + requests + jsonschema |
| 用例总数 | **56** 条，实跑 **56/56 通过**，耗时约 173s |
| 报告 | pytest-html 自包含报告 + Markdown 汇总 |
| 发现缺陷 | **3 个真实缺陷**（2 严重 + 1 一般）+ 2 个行为观察项 |

## 三、测试策略

### 3.1 测试分层

```
tests/
├── test_01_registration.py  # T01 注册流程：正常/重复/边界/注入/契约（15 条）
├── test_02_login_token.py   # T02 登录与 Token：签发/校验/失效/防枚举（10 条）
├── test_03_password.py      # T03 密码链路：修改/重置/确认/强度校验（8 条）
├── test_04_username.py      # T04 用户名链路：变更/边界/权限（8 条）
├── test_05_config_matrix.py # T05 配置矩阵：独立子进程复现缺陷（4 条）
├── test_06_contract.py      # T06 JSON Schema 契约一致性（6 条）
└── test_07_concurrency.py   # T07 并发唯一性/幂等/脏写（4 条）
```

配置矩阵测试采用**独立子进程**执行，原因见「测试方法论要点」。

### 3.2 用例设计方法

| 方法 | 落地场景 |
| --- | --- |
| 等价类划分 | 有效值 / 空值 / 非法枚举 / 类型错误（`null`、`[]`、超长串） |
| 边界值分析 | 用户名超长（300 字符）、空用户名、弱密码长度下限 |
| 场景法 | 注册→登录→改密码→改用户名→登出 的完整生命周期串联 |
| 状态迁移 | 未认证 → 已认证 → Token 失效 各状态下的接口可达性 |
| 错误推测 | 在 `LOGIN_FIELD ≠ USERNAME_FIELD` 等非常规配置下探测崩溃路径 |
| 判定表 | 登录字段 × 密码正确与否 的组合 |

### 3.3 数据隔离

- `live_server` fixture 提供真实 HTTP 端口，请求走完整 WSGI 栈（含中间件、鉴权、序列化）
- `unique_username` fixture 基于 UUID 生成唯一用户名，**用例间零共享、不依赖执行顺序**
- 用例产生的数据由 pytest-django 的事务回滚自动清理
- 测试库使用**文件型 SQLite**，使 LiveServer 线程与主线程共享同一份数据，
  同时支持并发用例（`:memory:` 按连接隔离，会导致跨线程不可见）
- 全部断言基于响应状态码与响应体，不依赖数据库直查，保证测的是"外部可观测行为"

## 四、缺陷发现

### BUG-01：`set_username` 在 `LOGIN_FIELD` 与 `USERNAME_FIELD` 不一致时抛出未捕获 `KeyError`（HTTP 500）

- **上游 Issue**：sunscrapers/djoser#922（原作者自述"基于静态分析，未实际执行"）
- **本项目结论**：**实际执行确认可复现**，并补充了原文未覆盖的 `reset_username_confirm` 第二处触发点
- **触发条件**：`DJOSER = {"LOGIN_FIELD": "email"}` 且用户模型 `USERNAME_FIELD = "username"`
- **实际结果**：HTTP 500，服务端抛出未捕获异常 `KeyError: 'new_username'`
- **预期结果**：应正常改名，或返回 400 校验错误，而非 500
- **根因定位**：`djoser/views.py:269` 与 `djoser/views.py:296`

  ```python
  # 序列化器层按 settings.LOGIN_FIELD 重命名字段
  self.fields[f"new_{self.username_field}"] = self.fields.pop(self.username_field)
  # 视图层却硬编码 User.USERNAME_FIELD 去取
  new_username = serializer.data["new_" + User.USERNAME_FIELD]
  ```

  两层分别使用 `settings.LOGIN_FIELD` 与 `User.USERNAME_FIELD`，二者不一致时取不到键。
- **影响面**：任何使用邮箱登录的用户模型（生产中极常见的配置）调用改用户名 / 重置用户名接口即 500
- **复现方式**：见 `tests/test_05_config_matrix.py`（独立子进程，在进程边界上复现）

### BUG-02：用户注册接口在 `LOGIN_FIELD` 与 `USERNAME_FIELD` 不一致时抛出未捕获 `TypeError`（HTTP 500）

- **上游 Issue**：无 —— 本项目在配置矩阵测试中**新发现**
- **触发条件**：`DJOSER = {"LOGIN_FIELD": "email"}` 且用户模型 `USERNAME_FIELD = "username"`
- **实际结果**：HTTP 500，服务端抛出未捕获异常
  `TypeError: UserManager.create_user() missing 1 required positional argument: 'username'`
- **预期结果**：应正常注册，或明确拒绝该配置组合，而非 500
- **根因定位**：`djoser/serializers.py:62-68`

  ```python
  class Meta:
      model = User
      fields = tuple(User.REQUIRED_FIELDS) + (
          settings.LOGIN_FIELD,   # "email" 时，username 完全不在可写字段中
          settings.USER_ID_FIELD,
          "password",
      )
  ```

  Django 默认 User 模型要求 `username` 必填，但该字段未被纳入 `fields`，
  最终在 `create_user()` 处抛 `TypeError`。

### 结论：该配置组合整体不可用

BUG-01 与 BUG-02 共同指向同一结论 —— 当 `LOGIN_FIELD ≠ USERNAME_FIELD` 时，
djoser 的注册、认证、改名三条主链路**全部断裂**：

| 链路 | 状态 |
| --- | --- |
| 注册 | HTTP 500（BUG-02） |
| 邮箱登录 | HTTP 400（认证后端按 `USERNAME_FIELD` 查询，匹配不到用户） |
| 改用户名 | HTTP 500（BUG-01） |

完整分析、复现步骤与修复建议见 [`docs/缺陷报告.md`](docs/缺陷报告.md)。

## 五、运行方式

```bash
# 安装依赖
pip install -r requirements.txt

# 运行全量测试并生成报告
pytest -v --html=reports/report.html --self-contained-html

# 只跑配置矩阵（缺陷复现，需启动子进程）
pytest -m slow -v

# 只跑并发用例
pytest -m concurrency -v

# 只跑契约校验
pytest -m contract -v
```

## 六、项目价值

本项目的价值不在"跑通多少条用例"，而在四点：

1. **缺陷可验证**：每一条缺陷都有可复现的最小脚本、真实的 HTTP 响应码与定位到行的根因分析。
2. **测试即文档**：用例结构直接对应上游 issue 的编号与讨论，第三方可直接复用。
3. **工程化完整**：分层目录、fixture 隔离、参数化驱动、契约校验、并发测试齐备。
4. **方法论可迁移**：配置矩阵测试、进程边界验证、探针顺序去污染等方法，可直接复用到其他开源项目的质量评估。

## 七、上游贡献

- 本项目产出的缺陷对应上游 issue：
  - https://github.com/sunscrapers/djoser/issues/922 （`set_username` 抛未捕获 `KeyError`）
- 测试工程自身开源，可作为 djoser 的补充测试套件回馈社区。

## 八、目录结构

```
djoser-api-qa/
├── requirements.txt
├── pytest.ini
├── README.md
├── conftest.py                    # 根级 fixture：live_server、HTTP client、用户工厂
├── testenv/
│   ├── __init__.py
│   ├── settings.py                # 默认配置（LOGIN_FIELD == USERNAME_FIELD）
│   ├── settings_email_login.py    # 非常规配置（LOGIN_FIELD=email），供配置矩阵子进程使用
│   └── urls.py                    # 路由
├── tests/
│   ├── test_01_registration.py    # T01 注册（15 条）
│   ├── test_02_login_token.py     # T02 登录与 Token（10 条）
│   ├── test_03_password.py        # T03 密码管理（8 条）
│   ├── test_04_username.py        # T04 用户名管理（8 条）
│   ├── test_05_config_matrix.py   # T05 配置矩阵（4 条，子进程）
│   ├── test_06_contract.py        # T06 契约与 Schema（6 条）
│   └── test_07_concurrency.py     # T07 并发与幂等（4 条）
├── docs/
│   ├── 测试计划.md
│   ├── 测试用例清单.md
│   ├── 缺陷报告.md
│   ├── 测试总结报告.md
│   └── github-actions-ci.yml      # CI 配置副本（见下方说明）
└── .github/
    └── PULL_REQUEST_TEMPLATE.md
```

## 九、关于 CI 配置

GitHub Actions 的工作流文件位于 `docs/github-actions-ci.yml`。

> **说明**：本仓库的代码是通过 GitHub App 推送的，该 App 的写入策略禁止直接创建
> `.github/workflows/` 下的文件（防止 CI 配置注入）。因此 CI 配置以副本形式保留在
> `docs/` 下。**若要启用 CI**，将 `docs/github-actions-ci.yml` 的内容
> （去掉开头的注释说明部分）复制到 `.github/workflows/ci.yml` 即可。

该 CI 配置包含：

- Python 3.10 / 3.11 / 3.12 三版本矩阵
- 每周一 03:00 UTC 定时回归（监控上游 djoser 发布是否引入回归）
- 并发用例额外重复 3 轮，用于识别偶发失败
- pytest-html 报告作为 artifact 归档

## 十、测试方法论要点

本项目在执行中沉淀了三条可复用的结论，均已写入代码注释：

1. **配置类缺陷必须在进程边界上验证** —— djoser 的 `UserCreateSerializer.Meta.fields`
   在模块导入时就把 `settings.LOGIN_FIELD` 求值为静态元组，运行期 `override_settings`
   修改配置不会使其重新计算。因此配置矩阵测试采用独立子进程。
2. **探针执行顺序必须避免自我污染** —— 改名探测会真实修改用户名，若排在登录探测之前，
   会导致后续登录必然失败、探针结果失真。
3. **数据库选型影响跨线程可见性** —— `:memory:` SQLite 按连接隔离，
   LiveServer 线程看不到主线程创建的数据；改用文件型 SQLite 解决。
