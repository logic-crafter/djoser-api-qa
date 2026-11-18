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
| 被测版本 | djoser 2.3.x / Django 5.2.x / DRF 3.x |
| 被测接口数 | 12+（注册 / 登录 / Token / 用户 / 密码 / 用户名） |
| 测试方式 | 黑盒 HTTP（`requests` + Django LiveServer）+ 白盒源码定位 |
| 运行框架 | pytest 8.x + requests + jsonschema |
| 报告 | pytest-html + Markdown 汇总 |
| CI | GitHub Actions（ubuntu-latest，Python 3.11 / 3.12 矩阵） |

## 三、测试策略

### 3.1 测试分层

```
tests/
├── conftest.py              # 全局 fixture：live server、HTTP client、用户工厂、数据清理
├── test_01_registration.py  # 注册流程：正常/重复/边界/注入/契约
├── test_02_login_token.py   # 登录与 Token：签发/校验/失效/边界
├── test_03_password.py      # 密码链路：修改/重置/确认/强度校验
├── test_04_username.py      # 用户名链路：变更/重置/确认 ← 缺陷集中区
├── test_05_contract.py      # OpenAPI 契约一致性 + Schema 校验
├── test_06_concurrency.py   # 并发注册/并发改名的幂等与竞态
└── test_07_regression.py    # 已发现缺陷的回归锁（xfail 标记）
```

### 3.2 用例设计方法

| 方法 | 落地场景 |
| --- | --- |
| 等价类划分 | 有效值 / 空值 / 非法枚举 / 类型错误（`null`、`[]`、`123`、超长串） |
| 边界值分析 | 用户名长度 1/150/151、密码长度 1/8/9、分页 size、批量上限 |
| 场景法 | 注册→登录→改密码→改用户名→删号的完整生命周期串联 |
| 状态迁移 | 未认证 → 已认证 → Token 失效 → 用户禁用 各状态下的接口可达性 |
| 错误推测 | 在 `LOGIN_FIELD ≠ USERNAME_FIELD` 等非常规配置下探测崩溃路径 |

### 3.3 数据隔离

- `live_server` fixture 提供真实 HTTP 端口，请求走完整 WSGI 栈（含中间件、鉴权、序列化）
- `unique_username` fixture 基于 UUID 生成唯一用户名，**不依赖用例执行顺序**
- 每个用例结束清理其创建的用户与 Token，避免用例间数据污染
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
- **状态**：已向上游提交反馈（见"上游贡献"章节）

### BUG-02：详见 `docs/缺陷报告.md`

## 五、运行方式

```bash
# 安装依赖
pip install -r requirements.txt

# 运行全量测试并生成报告
pytest -v --html=reports/report.html --self-contained-html

# 只跑缺陷相关用例
pytest tests/test_04_username.py -v

# 只跑契约校验
pytest -m contract -v
```

## 六、项目价值

本项目的价值不在"跑通多少条用例"，而在三点：

1. **缺陷可验证**：每一条缺陷都有可复现的最小脚本、真实的 HTTP 响应码与定位到行的根因分析，
   并沉淀为 `xfail` 回归用例，避免修复后回归。
2. **测试即文档**：用例结构直接对应上游 issue 的编号与讨论，第三方可直接复用。
3. **工程化完整**：分层目录、fixture 隔离、参数化驱动、契约校验、并发测试、CI 流水线齐备。

## 七、上游贡献

- 本项目产出的缺陷已提交至上游仓库 issue：
  - https://github.com/sunscrapers/djoser/issues/922
- 测试工程自身开源，可作为 djoser 的补充测试套件回馈社区。

## 八、目录结构

```
djoser-api-qa/
├── requirements.txt
├── pytest.ini
├── README.md
├── conftest.py              # 根级配置与 fixture
├── testenv/
│   ├── __init__.py
│   ├── settings.py          # Django 配置（多套配置用于覆盖非常规场景）
│   └── urls.py              # 路由
├── tests/                   # 测试用例
├── docs/
│   ├── 测试计划.md
│   ├── 测试用例清单.md
│   ├── 缺陷报告.md
│   └── 测试总结报告.md
└── .github/workflows/ci.yml
```
