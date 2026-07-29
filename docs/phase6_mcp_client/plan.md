# OkCode 第六阶段：MCP 客户端与外部工具接入 Plan

## 架构概览

本阶段采用四层单向结构，保持现有本地工具和 Agent Loop 不变：

```text
用户/项目 MCP 配置
        ↓
McpConfigLoader ──→ 已合并的 Server 配置
        ↓
McpClientManager ──→ 独立连接、握手、分页 tools/list、关闭
        ↓
McpToolAdapter ──→ ToolRegistry ──→ ToolExecutor ──→ Agent Loop
```

CLI 装配顺序调整为：加载现有 Provider 配置和 MCP 配置，先注册六个内置工具，再异步发现并注册远端工具，最后用完整工具名加载权限规则。退出时先关闭 MCP 连接，再关闭 Provider。某个 Server 的发现失败只记录启动告警，不影响其余 Server、内置工具或 REPL。

`McpConfigLoader` 只读取两份 `.okcode/config.yaml` 的 `mcp_servers`，不改变当前启动目录 `config.yaml` 的 Provider 配置语义。`McpClientManager` 使用官方 MCP Python SDK 完成 JSON-RPC、stdio/HTTP 传输、初始化和调用。`McpToolAdapter` 将远端工具包装为现有 `Tool` 接口；CLI 与终端负责告警和资源生命周期。

## 核心数据结构与接口

### 配置模型

`AppConfig` 保持只描述模型 Provider。新增独立的 MCP 配置加载结果，避免用户级或项目级 MCP 配置影响现有启动目录 `config.yaml`。

```python
McpConfigPaths(
    user=Path.home() / ".okcode" / "config.yaml",
    project=workspace_root / ".okcode" / "config.yaml",
)

StdioMcpServerConfig(
    name, command, args, env,
)

StreamableHttpMcpServerConfig(
    name, url, headers,
)

McpConfig(
    servers: tuple[StdioMcpServerConfig | StreamableHttpMcpServerConfig, ...],
)
```

配置格式固定为：

```yaml
mcp_servers:
  filesystem:
    transport: stdio
    command: uvx
    args: ["@modelcontextprotocol/server-filesystem", "${WORKSPACE_ROOT}"]
    env:
      API_TOKEN: "${FILESYSTEM_TOKEN}"

  remote_search:
    transport: streamable_http
    url: "https://example.com/mcp"
    headers:
      Authorization: "Bearer ${SEARCH_TOKEN}"
```

stdio 的 `env` 覆盖式合并到当前进程环境，而不是替换整个环境，确保 Windows 子进程仍保有 `PATH` 等基础变量。配置加载器只读取根节点的 `mcp_servers`；文件不存在视为未配置，文件存在但格式无效则报 `ConfigError`。

### 运行期对象

```python
McpClientManager
    async discover_tools() -> McpDiscoveryResult
    async call_tool(server_name, remote_tool_name, arguments) -> McpCallResult
    async aclose() -> None

McpDiscoveryResult(
    tools: tuple[McpRemoteTool, ...],
    warnings: tuple[McpDiscoveryWarning, ...],
)

McpRemoteTool  # 实现现有 Tool 协议
    definition -> ToolDefinition
    async execute(arguments) -> ToolOutput
```

`McpClientManager` 为每个成功连接的 Server 私有保存会话、底层传输资源和调用锁。`discover_tools()` 返回可注册的适配工具与非致命告警；CLI 将工具注册后，再加载权限规则，确保远端工具名也能出现在权限规则中。

### 工具与结果映射

- 适配工具名称固定为 `mcp__<server>__<remote_tool>`，调用时自动还原原始远端工具名。
- 工具描述与输入 JSON Schema 原样映射到 `ToolDefinition`。
- 所有远端工具默认标记为有副作用、权限目标为 `NONE`，因此继续接受既有权限模式和人工确认控制。
- 文本结果按 MCP 内容块原顺序拼接；`structuredContent` 归入现有工具结果的 JSON 数据。
- 服务端返回 `isError=true` 时转换为结构化工具失败；连接、协议或已断开会话失败也转换为结构化工具失败，不抛到 Agent Loop。
- 仅含图片、音频或嵌入资源的结果返回“当前不支持该结果类型”的结构化失败。
- 新增 MCP 专用错误码，区分远端工具错误、远端连接不可用和不支持的结果类型。

### 名称与会话约束

生成后的工具名必须满足当前 Provider 共用的函数名限制；不合规或与已有名称冲突的单个远端工具跳过并产生该 Server 的启动告警，不影响同 Server 的其他合法工具。

每个 Server 使用独立调用锁串行执行远端调用。这样既避免同一 MCP 会话上的并发状态问题，也与“所有远端工具默认有副作用”的权限策略一致。连接一旦在运行期断开，记录为不可用并直接返回失败，本阶段不自动重连。

## 启动、调用与退出流程

### 启动与发现

```text
CLI
  → 加载现有 Provider 配置
  → 加载并合并两层 MCP 配置（配置错误则退出码 2）
  → 创建本地六工具 Registry
  → 并行发现每个 MCP Server
      → 建立 stdio 或 Streamable HTTP 传输
      → 创建 ClientSession
      → initialize
      → 分页 tools/list，直到没有 nextCursor
      → 保存成功连接与远端工具元数据
  → 注册合法的 McpRemoteTool
  → 以“内置 + 远端”完整名称加载权限规则
  → 输出脱敏的 Server 级告警
  → 启动 REPL
```

每个 Server 的“连接、初始化、完整工具发现”共享 10 秒启动时限。发现任务使用相互隔离的并发任务执行：任一任务内部捕获超时、传输和协议异常，关闭自己的资源并返回告警，不向其他任务传播异常。

工具列表按 Server 名称和最终工具名稳定排序。分页响应出现重复游标、无效工具元数据或不合法 Schema 时，仅使该 Server 发现失败并产生告警。

### 工具调用

```text
Agent 选择 mcp__server__tool
  → ToolExecutor：JSON / Schema / 权限预检
  → McpRemoteTool.execute
  → Manager 取得对应 Server 调用锁
  → ClientSession.call_tool(原始工具名, 参数)
  → 将 MCP 结果转为 ToolOutput 或 ToolFailure
  → ToolExecutor 统一转换为 ToolExecutionResult
  → Agent Loop 回灌历史并继续推理
```

远端调用沿用现有 30 秒工具执行时限。MCP SDK 负责 JSON-RPC 2.0 的请求 ID、异步响应关联、初始化通知与协议编解码；OkCode 不维护另一套手写的 JSON-RPC 收发器。

调用期间发生传输或协议异常时，Manager 将该连接标记为不可用，当前调用返回结构化失败；后续对同一 Server 的调用直接失败，不尝试重连。其他 Server 不受影响。

### Streamable HTTP 与 stdio

- HTTP：Manager 在 Server 生命周期内创建带已展开 `headers` 的 HTTP 客户端，将其交给 SDK 的 Streamable HTTP 传输，并随连接资源栈一并关闭。
- stdio：Manager 将当前 `os.environ` 与配置 `env` 合并后传给 SDK；配置值覆盖同名环境变量。
- 两类传输都使用同一个 `ClientSession` 会话抽象，因此初始化、分页发现、调用、错误处理和关闭逻辑一致。

### 退出清理

CLI 的 `finally` 块按以下顺序处理：

```text
逐个关闭 MCP Server 资源栈
  → 关闭 LLM Provider
  → 关闭 asyncio.Runner
```

每个 MCP 连接的关闭错误都被单独捕获，仅记录为安全告警，保证后续连接、Provider 和事件循环仍能完成清理。

## 文件组织、依赖与测试设计

### 文件组织

```text
src/okcode/
├── cli.py                         # 装配 MCP 发现、告警与退出清理
├── terminal.py                    # 显示脱敏的 MCP Server 启动告警
├── tools/
│   └── models.py                  # 新增 MCP 专用工具错误码
└── mcp/
    ├── __init__.py                # 对外导出 MCP 装配入口
    ├── models.py                  # 配置、发现结果与告警数据结构
    ├── config.py                  # 两层 YAML 读取、合并、校验与变量展开
    ├── manager.py                 # stdio/HTTP 连接、握手、分页发现、调用、清理
    └── tool.py                    # McpRemoteTool 到现有 Tool 接口的适配

tests/
├── unit/
│   ├── test_mcp_config.py         # 合并、校验、变量展开与敏感值脱敏
│   ├── test_mcp_tool.py           # 名称、Schema、结果与错误映射
│   ├── test_mcp_manager.py        # 分页、超时、断连、隔离与清理
│   ├── test_cli.py                # 装配顺序、告警与最终清理
│   └── test_terminal.py           # MCP 告警输出
└── integration/
    ├── test_mcp_stdio.py          # 真实 stdio MCP Server 完整链路
    └── test_mcp_streamable_http.py # 真实 HTTP MCP Server、请求头与完整链路
```

`src/okcode/config.py` 不承载 MCP 两层配置逻辑，现有 Provider 配置读取行为保持不变。MCP 模块只依赖 `tools` 的抽象和数据模型，不能反向依赖 Agent、Provider、终端或 CLI。

### 依赖与技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| MCP 协议实现 | 官方 `mcp` Python SDK | SDK 已覆盖会话、JSON-RPC、stdio 与 Streamable HTTP，避免自维护协议细节。 |
| HTTP 连接 | 使用当前 SDK 支持的异步 HTTP 客户端并随会话关闭 | 支持配置头且不泄漏连接资源。 |
| Provider 配置 | 与 MCP 两层配置分离 | 保持当前 `<项目>/config.yaml` 兼容。 |
| 命名规则 | `mcp__<server>__<tool>` | 避免跨 Server 和内置工具冲突。 |
| 权限分类 | 全部远端工具默认有副作用 | Server 声明不能作为安全边界，默认确认更稳妥。 |
| 会话并发 | 单个 Server 串行，Server 间并行 | 避免共享会话状态问题，并符合副作用工具调度语义。 |
| 故障策略 | 首次失败后标记不可用，不重连 | 严格符合本阶段范围，行为可预期。 |

`pyproject.toml` 增加官方 `mcp` 运行依赖并更新 `uv.lock`；Streamable HTTP 所需的 SDK 配套 HTTP 客户端按最终锁定 SDK 版本声明和使用。

### 测试层次

- 配置单测：缺失文件、两层覆盖、字段互斥、非法 URL、变量展开、未定义变量、错误信息不含密钥。
- 适配单测：唯一名称、原始 Schema 保留、文本与结构化结果、`isError`、非文本结果、连接失败映射。
- Manager 单测：多页工具列表、重复游标、连接超时、单 Server 失败不影响其他 Server、断连后不重连、关闭错误隔离。
- 传输集成测试：使用 SDK 提供的受控 MCP 测试 Server，分别走真实 stdio 与 loopback Streamable HTTP；HTTP 测试断言自定义请求头确实到达服务端。
- 回归测试：运行全部现有测试，确认本地工具、权限、Agent Loop、Provider SSE 与终端行为未回退。
