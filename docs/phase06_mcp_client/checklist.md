# OkCode 第六阶段：MCP 客户端与外部工具接入 Checklist

> 每项都通过运行测试或观察真实行为验证，重点检查两层配置、两种传输、工具适配、连接隔离和退出清理是否真正完成。

## 实现完整性

- [x] 用户级与项目级 `.okcode/config.yaml` 可独立缺失；两者同时存在时，未重名 Server 合并、同名 Server 由项目级完整覆盖。（验证：配置矩阵单测。）
- [x] `stdio` 与 `streamable_http` 的必填字段、互斥字段、URL、字符串映射和 `${VAR}` 均被严格校验。（验证：配置负例单测。）
- [x] 未定义变量的配置错误包含来源、Server 和字段，但不含展开后的密钥、请求头或环境变量值。（验证：对错误文本作敏感串断言。）
- [x] stdio 子进程环境继承当前环境，配置 `env` 仅覆盖同名变量。（验证：传输替身检查 `PATH` 保留与覆盖值。）
- [x] 每个成功 Server 完成初始化、完整分页 `tools/list` 后才产生可注册工具；重复游标、无效元数据或发现异常只影响该 Server。（验证：Manager 单测。）
- [x] 远端工具以 `mcp__<server>__<tool>` 注册，描述和 Schema 保留，默认有副作用且权限目标为 `NONE`。（验证：适配层单测和 Registry 断言。）
- [x] 文本、结构化结果、`isError`、非文本结果和传输失败分别映射为预期工具输出或结构化错误。（验证：适配层单测。）
- [x] 同一 Server 调用串行，不同 Server 可独立调用；运行期断连后仅该 Server 变为不可用且不会自动重连。（验证：并发与断连单测。）
- [x] 发现和调用始终经过 MCP SDK 会话，不存在自行维护 JSON-RPC 请求 ID 或手写消息解析的旁路。（验证：代码审查和真实传输集成测试。）
- [x] 正常退出、发现失败和关闭异常时，已创建的会话、HTTP 客户端、传输与子进程均被清理，且单个关闭失败不阻断其他清理。（验证：资源替身与集成测试。）

## 集成

- [x] CLI 先加载 MCP 配置、发现并注册远端工具，再以“内置 + 远端”完整名称加载权限规则。（验证：CLI 装配顺序测试。）
- [x] MCP 配置无效时 CLI 显示配置错误、返回退出码 `2`，且不创建 Provider 或 App。（验证：CLI 工厂替身测试。）
- [x] 一个 Server 连接、初始化、发现或调用失败时，其他可用 MCP Server、六项内置工具与 REPL 仍可使用。（验证：多 Server 隔离测试。）
- [x] 单个 Server 发现超时受 10 秒边界约束，不会无限阻塞其他发现任务或 REPL 启动。（验证：可控慢 Server 测试。）
- [x] 远端工具继续经过既有 JSON Schema 校验、权限确认、30 秒执行超时、结果截断与 Agent Loop 历史回灌。（验证：`ToolExecutor` 和受控 Provider 集成测试。）
- [x] 同名远端工具、与内置工具同名的远端工具，以及不符合 Provider 名称限制的远端工具，均不会破坏已注册工具。（验证：名称冲突与跳过告警测试。）
- [x] HTTP Server 收到配置声明的自定义请求头，且终端告警与错误消息不显示 URL、请求头或环境变量值。（验证：HTTP 集成测试和终端捕获测试。）
- [x] CLI 退出时先关闭 MCP Manager，再关闭 Provider 和 `asyncio.Runner`。（验证：关闭顺序测试。）

## 终端与文档

- [x] 非致命 Server 失败在 REPL 前显示简体中文、含 Server 名与阶段的脱敏告警。（验证：`Console` 可见文本断言。）
- [x] MCP 告警不破坏既有思考括号、回答分隔线、工具状态和 Token 输出顺序。（验证：终端全量单测。）
- [x] README 给出两层配置路径、合并优先级、stdio/HTTP 示例、唯一工具命名、默认权限语义和不支持范围。（验证：与有效配置测试对照。）
- [x] README 不再声称“尚未接入真实 MCP”，且不含真实凭据。（验证：文本检查。）

## 自动化验证

- [x] MCP 配置、工具适配、Manager、CLI 和终端单测全部通过。  
  验证：`uv run pytest tests/unit/test_mcp_config.py tests/unit/test_mcp_tool.py tests/unit/test_mcp_manager.py tests/unit/test_cli.py tests/unit/test_terminal.py -q`

- [x] stdio 与 Streamable HTTP 真实传输测试通过。  
  验证：`uv run pytest tests/integration/test_mcp_stdio.py tests/integration/test_mcp_streamable_http.py -q`

- [x] 全部项目测试、Ruff 格式与静态检查通过。  
  验证：`uv run pytest -q`、`uv run ruff format --check .`、`uv run ruff check .`

## 端到端场景

- [x] 场景 1：项目级配置覆盖用户级同名 stdio Server，并额外保留用户级另一个 Server。  
  验证：启动后只连接项目级同名 Server，两个未冲突 Server 的工具均可用。

- [x] 场景 2：模型通过 Agent Loop 调用 `mcp__stdio_server__echo`。  
  验证：参数经 Schema 与权限链路后到达真实 stdio Server；文本与结构化结果回灌给下一次模型请求。

- [x] 场景 3：HTTP MCP Server 需要 `Authorization` 请求头。  
  验证：`${TOKEN}` 展开后请求头到达 Server；工具发现与调用成功；终端没有输出 Token。

- [x] 场景 4：同时配置两个可用 Server 和一个不可用 Server。  
  验证：可用工具注册、不可用 Server 产生告警、内置工具仍可执行；断开其中一个已发现 Server 后，其后续调用失败但其他 Server 继续成功。

- [x] 场景 5：退出包含 stdio 与 HTTP 连接的 OkCode。  
  验证：全部资源关闭；即使一个关闭操作抛错，Provider 和其余连接仍完成清理。
