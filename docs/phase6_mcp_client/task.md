# OkCode 第六阶段：MCP 客户端与外部工具接入 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml`、`uv.lock` | 增加官方 MCP SDK 依赖 |
| 新建 | `src/okcode/mcp/__init__.py`、`models.py` | MCP 公共接口、配置与发现领域模型 |
| 新建 | `src/okcode/mcp/config.py` | 两层配置、校验、合并与变量展开 |
| 新建 | `src/okcode/mcp/tool.py` | 远端工具适配现有 `Tool` 接口 |
| 新建 | `src/okcode/mcp/manager.py` | 两类传输、会话、发现、调用与清理 |
| 修改 | `src/okcode/tools/models.py` | MCP 专用错误码 |
| 修改 | `src/okcode/cli.py`、`terminal.py` | 启动装配、告警和退出清理 |
| 修改 | `README.md` | MCP 配置和范围说明 |
| 新建/修改 | `tests/unit/test_mcp_*.py`、`tests/integration/test_mcp_*.py` | 单元、传输集成与回归覆盖 |

## T1：锁定 MCP SDK 依赖

**文件：** `pyproject.toml`、`uv.lock`

**依赖：** 无

**步骤：**

1. 添加官方 `mcp` 运行依赖并更新锁文件。
2. 确认锁定版本支持 Python 3.12、stdio 客户端和 Streamable HTTP 客户端。
3. 确认 SDK 及其所需 HTTP 客户端模块可以在项目虚拟环境中导入。

**验证：** 执行 `uv sync --all-groups` 和 SDK 导入检查，预期均成功。

## T2：建立 MCP 领域模型与错误码

**文件：** `src/okcode/mcp/__init__.py`、`src/okcode/mcp/models.py`、`src/okcode/tools/models.py`

**依赖：** T1

**步骤：**

1. 定义 stdio 与 Streamable HTTP Server 配置、配置路径、发现结果、脱敏告警和调用结果。
2. 定义调用结果的文本、结构化数据和错误状态边界，不向上层泄漏 SDK 对象。
3. 增加远端工具错误、连接不可用和不支持结果类型的工具错误码。
4. 从 `mcp` 包导出后续模块需要的稳定公共入口，避免 CLI 依赖内部实现。

**验证：** 新增模型构造断言，执行 `uv run pytest tests/unit/test_mcp_config.py tests/unit/test_mcp_tool.py -q`，预期通过。

## T3：实现两层配置加载

**文件：** `src/okcode/mcp/config.py`、`tests/unit/test_mcp_config.py`

**依赖：** T2

**步骤：**

1. 定位用户级 `%USERPROFILE%/.okcode/config.yaml` 与项目级 `.okcode/config.yaml`。
2. 读取两份可选 YAML，仅接受根节点的 `mcp_servers`，按项目级覆盖用户级同名完整配置。
3. 校验 `transport`、stdio 的 `command`/`args`/`env`、HTTP 的 `url`/`headers` 与字段互斥关系。
4. 对声明字段展开 `${VAR}`；变量未定义或语法无效时产生包含来源、Server 和字段而不含敏感值的 `ConfigError`。
5. 对 stdio 配置构造继承当前进程环境并覆盖同名变量的最终环境映射。

**验证：** 执行 `uv run pytest tests/unit/test_mcp_config.py -q`，覆盖缺失文件、两层覆盖、非法字段、非法 URL、变量缺失和环境继承。

## T4：实现远端工具适配层

**文件：** `src/okcode/mcp/tool.py`、`tests/unit/test_mcp_tool.py`

**依赖：** T2

**步骤：**

1. 实现符合现有 `Tool` 协议的 `McpRemoteTool`，保存 Server 名称、原始工具名和 Manager 调用入口。
2. 将远端描述和输入 Schema 映射为 `ToolDefinition`，工具名使用 `mcp__<server>__<tool>`，安全级别固定为有副作用。
3. 将文本内容块按原顺序合并，把结构化 JSON 写入 `ToolOutput.data`。
4. 将服务端 `isError`、连接不可用和仅非文本结果转换为对应 `ToolFailure`。
5. 验证生成工具名并拒绝不符合 Provider 共用函数名限制的远端工具。

**验证：** 执行 `uv run pytest tests/unit/test_mcp_tool.py -q`，断言原始工具名正确路由、定义可注册、结果映射完整。

## T5：实现单 Server 连接资源管理

**文件：** `src/okcode/mcp/manager.py`、`tests/unit/test_mcp_manager.py`

**依赖：** T1、T2

**步骤：**

1. 为每个成功 Server 保存会话、传输资源栈、调用锁和可用状态。
2. 建立 stdio 传输时传入合并后的环境；建立 HTTP 传输时创建带配置请求头的 SDK 支持 HTTP 客户端。
3. 将传输、会话和 HTTP 客户端按创建逆序纳入异步资源栈。
4. 实现单连接关闭和全部连接关闭，关闭一个连接异常时继续清理其他资源。

**验证：** 执行 `uv run pytest tests/unit/test_mcp_manager.py -q`，使用替身断言环境、请求头、资源关闭顺序和关闭隔离。

## T6：实现并行发现与分页工具列表

**文件：** `src/okcode/mcp/manager.py`、`tests/unit/test_mcp_manager.py`

**依赖：** T3、T5

**步骤：**

1. 对每个配置 Server 独立并行执行连接、初始化和 `tools/list` 分页发现。
2. 为单个 Server 的完整发现应用 10 秒时限，并将超时、协议与传输错误转换为脱敏告警。
3. 累积所有工具页，检测重复游标、重复工具名和无效远端工具元数据。
4. 仅为成功发现且名称合规的工具创建适配对象，并按 Server 与最终工具名稳定排序。
5. 发现失败时关闭该 Server 资源，不让异常传播到其他发现任务。

**验证：** 执行 `uv run pytest tests/unit/test_mcp_manager.py -q`，断言分页、失败隔离、超时和稳定排序。

## T7：实现调用锁与断连语义

**文件：** `src/okcode/mcp/manager.py`、`tests/unit/test_mcp_manager.py`

**依赖：** T5

**步骤：**

1. 按 Server 调用锁串行执行 SDK `call_tool`，同时允许不同 Server 独立调用。
2. 将 SDK 调用结果转为 MCP 模块内部调用结果，供适配层统一映射。
3. 调用期间发生传输或协议异常时，将对应连接标记为不可用并关闭资源。
4. 对已不可用 Server 的后续调用直接返回连接不可用错误，不触发重连。

**验证：** 执行 `uv run pytest tests/unit/test_mcp_manager.py -q`，断言同 Server 调用不重叠、断连后不重连、其他 Server 仍可调用。

## T8：接入 CLI、权限与终端告警

**文件：** `src/okcode/cli.py`、`src/okcode/terminal.py`、`tests/unit/test_cli.py`、`tests/unit/test_terminal.py`

**依赖：** T3、T4、T6、T7

**步骤：**

1. CLI 在创建内置注册表后加载 MCP 配置、发现并注册远端工具。
2. 用包含远端工具的完整名称集合加载权限规则，再构造权限管理器、执行器和会话。
3. 将发现告警以不含 URL、请求头或环境变量值的终端消息显示在 REPL 前。
4. 在 `finally` 中先关闭 MCP Manager，再关闭 Provider 和 `asyncio.Runner`。
5. 保持现有配置错误退出码、普通启动、Provider 生命周期和终端事件渲染不变。

**验证：** 执行 `uv run pytest tests/unit/test_cli.py tests/unit/test_terminal.py -q`，断言装配顺序、配置错误、告警脱敏和清理顺序。

## T9：添加 stdio 端到端测试

**文件：** `tests/integration/test_mcp_stdio.py`、必要的测试辅助文件

**依赖：** T6、T7

**步骤：**

1. 使用 SDK 提供的受控 MCP 测试 Server 构建 stdio 测试入口。
2. 配置 Server 并启动 Manager，验证真实握手、工具发现、唯一命名和调用结果。
3. 退出 Manager 后断言 stdio 子进程和传输被回收。

**验证：** 执行 `uv run pytest tests/integration/test_mcp_stdio.py -q`，预期通过。

## T10：添加 Streamable HTTP 端到端测试

**文件：** `tests/integration/test_mcp_streamable_http.py`、必要的测试辅助文件

**依赖：** T6、T7

**步骤：**

1. 使用 SDK 提供的受控 MCP 测试 Server 启动 loopback Streamable HTTP 端点。
2. 配置 HTTP Server 与自定义请求头，验证真实握手、发现和调用。
3. 在测试 Server 端断言自定义请求头到达，并验证关闭后连接资源释放。

**验证：** 执行 `uv run pytest tests/integration/test_mcp_streamable_http.py -q`，预期通过。

## T11：更新使用文档

**文件：** `README.md`

**依赖：** T8

**步骤：**

1. 增加用户级与项目级 MCP 配置路径、合并优先级和完整 YAML 示例。
2. 说明远端工具命名规则、默认权限语义和单 Server 失败隔离。
3. 明确 MCP 资源、提示词、采样、健康检查和自动重连仍不在当前范围。
4. 删除“尚未接入真实 MCP”的旧范围描述。

**验证：** 对照 `tests/unit/test_mcp_config.py` 中的有效配置断言文档字段一致，并人工检查不包含真实凭据。

## T12：执行全量验证

**文件：** 仅修复前述范围内的发现问题

**依赖：** T1 至 T11

**步骤：**

1. 执行所有 MCP 单元测试与 stdio、HTTP 集成测试，修复范围内失败。
2. 执行全量测试，确认本地工具、权限、Agent Loop、Provider SSE 与终端行为未回归。
3. 执行 Ruff 格式与静态检查，修复本阶段新增代码的格式、导入和类型问题。
4. 复查 Git 状态，确认新增文件均属于本阶段范围。

**验证：** 依次执行：

```powershell
uv run pytest tests/unit/test_mcp_config.py tests/unit/test_mcp_tool.py tests/unit/test_mcp_manager.py tests/unit/test_cli.py tests/unit/test_terminal.py -q
uv run pytest tests/integration/test_mcp_stdio.py tests/integration/test_mcp_streamable_http.py -q
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
```

预期：全部测试与 Ruff 检查通过。

## 执行顺序

```text
T1 → T2 → T3 ──────┐
          └→ T4 ───┤
T1 + T2 → T5 → T6 ─┼→ T8 → T11
              └→ T7 ┤
T6 + T7 → T9、T10 ─┴→ T12
```
