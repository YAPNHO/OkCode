# OkCode 第八阶段：会话恢复与长期记忆 Checklist

> 每个条目必须通过命令、测试断言或终端可观察行为验证。不得以代码存在或人工推测替代验证。

## 指令与提示词

- [x] 三层 `AGENTS.md` 按项目根、项目 `.okcode/`、用户目录的顺序进入同一自定义指令区段，高优先级文本位于前面。（验证：`uv run pytest tests/unit/test_instructions.py -q`，并断言实际 `ProviderRequest` 的动态提示顺序。）
- [x] `@include` 可递归展开项目内文件；循环、超过最大深度、`..` 路径及解析后经符号链接越界的目标都被拒绝且包含来源定位。（验证：指令加载单元测试覆盖上述输入。）
- [x] 新会话的第一轮请求包含用户级和项目级记忆索引，但不包含任何旧会话普通消息。（验证：会话集成测试检查 `ProviderRequest.messages` 与 `prompt.dynamic_system`。）

## 会话存档与列表

- [x] 正常启动产生格式为 `YYYYMMDD-HHMMSS-xxxx` 的活动会话 ID，首轮成功提交后才创建 `<项目>/sessions/<id>.jsonl`。（验证：会话存储测试检查 ID、惰性创建和逐行 JSON。）
- [x] 成功自然结束的一轮会把用户、助手、工具调用和工具结果按原顺序追加到 JSONL，并可完整编解码 `provider_state`。（验证：会话存储与会话集成测试覆盖 OpenAI 和 Anthropic 重放数据。）
- [x] 会话列表只扫描 JSONL，显示 ID、首条用户消息标题、消息数和最后更新时间；不存在独立 meta 文件。（验证：存储测试创建多个日志并验证排序、字段和值来源。）
- [x] 超过 30 天的日志被清理且不出现在可恢复列表，未过期日志保持不变。（验证：固定时钟的清理测试。）

## 恢复与上下文

- [x] 输入 `/resume` 后终端显示可恢复会话表格；输入有效编号才开始恢复，取消、无会话或非法编号都保持当前会话不变。（验证：`uv run pytest tests/unit/test_terminal.py tests/unit/test_app.py -q`。）
- [x] 恢复 JSONL 时跳过单独坏行；末尾半行、缺失工具结果、重复或错配工具结果都只恢复最后一个合法消息边界。（验证：`uv run pytest tests/unit/test_sessions.py -q`。）
- [x] 恢复成功后，下一轮请求携带恢复历史；恢复后超出预算时先且只先执行一次已有摘要流程。（验证：会话测试观察摘要 Provider 请求和最终正常请求的顺序。）
- [x] 与最后活动时间相隔超过配置阈值时，下一轮请求携带一次时间跨度系统提醒；提醒不写进普通消息历史，也不会在后续请求重复。（验证：会话测试检查动态系统补充和历史内容。）
- [x] 恢复会重建上下文管理器的用户原文记录，并清空上一进程的摘要、Token 锚点和熔断状态。（验证：`uv run pytest tests/unit/test_context_manager.py tests/unit/test_conversation.py -q`。）

## 自动笔记与索引

- [x] 最终正式回答成功提交后，后台 Worker 接收到一个任务；请求没有工具、禁用提示缓存，并包含本轮消息及当前双索引。（验证：`uv run pytest tests/unit/test_memory_request.py tests/unit/test_memory_worker.py -q`。）
- [x] LLM 更新可创建或追加用户偏好、纠正反馈、项目知识和参考资料四类带 frontmatter 的 Markdown 笔记；LLM 返回无变更时不生成重复笔记。（验证：记忆请求和存储测试覆盖四类、创建、追加和无变更。）
- [x] 项目与用户笔记写入不同 `memory/` 目录，索引中的所有条目均引用有效笔记，且每份索引不超过 200 行和 25KB。（验证：`uv run pytest tests/unit/test_memory_store.py -q`。）
- [x] 后台 Provider、响应解析或落盘失败时，当前回答已提交、后续输入仍可处理，Worker 继续消费之后的任务。（验证：Worker 与会话测试注入失败后再投递成功任务。）

## 集成与回归

- [x] CLI 启动时完成过期清理、指令加载、Store/Journal/Worker/提示词来源装配；退出时先关闭 Worker，启动失败时不遗留线程。（验证：`uv run pytest tests/unit/test_cli.py -q`。）
- [x] 现有普通对话、Plan/Do、权限、内置工具、MCP、Provider 流和上下文压缩测试全部仍通过。（验证：`uv run pytest -q`。）
- [x] 代码格式与静态检查均通过，且测试临时会话、笔记和索引文件不进入仓库。（验证：`uv run ruff format --check .`、`uv run ruff check .`、`git status --short`。）

## 验收证据

- `uv run pytest tests/unit/test_instructions.py tests/unit/test_sessions.py tests/unit/test_memory_store.py tests/unit/test_memory_request.py tests/unit/test_memory_worker.py tests/unit/test_prompt_runtime.py tests/unit/test_context_manager.py tests/unit/test_conversation.py tests/unit/test_terminal.py tests/unit/test_app.py tests/unit/test_cli.py -q`：98 passed。
- `uv run pytest tests/unit/test_conversation.py tests/unit/test_prompt_runtime.py tests/unit/test_app.py -q`：33 passed。
- `uv run pytest -q`：228 passed。
- `uv run ruff format --check .`：133 files already formatted。
- `uv run ruff check .`：All checks passed。
- `git diff --check`：无空白错误；仅显示 Windows CRLF 提示。

## 端到端场景

- [x] **场景一：新会话带长期记忆。** 预置用户级偏好、项目知识和三层指令后启动 OkCode，第一轮请求同时看到合并指令和双索引，但没有上一会话的用户或助手消息。（验证：端到端替身测试检查首个 `ProviderRequest`。）
- [x] **场景二：用户主动恢复。** 先完成一个包含工具调用的会话并退出；再次启动后确认默认新会话，再输入 `/resume` 并选择旧会话。下一条普通请求包含完整恢复历史，随后可继续执行工具和得到回答。（验证：应用和会话集成测试覆盖整个选择至续聊流程。）
- [x] **场景三：中断后安全恢复。** 在日志末尾制造半行或无工具结果的助手调用，再通过 `/resume` 选择该日志。界面报告恢复进度，模型请求只收到完整前缀，且不会出现无效工具协议错误。（验证：会话恢复集成测试。）


