# OkCode 第十一阶段：Hooks 自动化机制 Checklist

> 每项必须通过运行代码或观察行为验证。验收时记录实际命令、关键输出和通过状态，不以代码阅读代替验证。

## 配置与匹配

- [ ] AC1：有效的 .okcode/hooks.yaml 能加载为已启用规则；缺少 event 或 action、未知字段、未知事件、错误规则类型时启动或刷新失败，并包含文件路径、hooks[index] 和字段定位。（验证：运行 uv run pytest tests/unit/test_hooks_config.py -q，观察合法配置成功、非法配置均抛出可定位 ConfigError）

- [ ] AC4：Hook 条件分别正确执行 exact、glob、regex 和 not 匹配；if 列表按 all 处理，all 与 any 分别具有“全部满足”和“任一满足”语义；混用 all/any、非法正则、错误字段和不兼容匹配类型在加载期失败。（验证：运行 uv run pytest tests/unit/test_matching.py tests/unit/test_hooks_config.py -q，观察所有正反例通过）

- [ ] 共享匹配器：既有权限 YAML 的裸精确和裸 glob 规则保持兼容，新的 exact、glob、regex、not 规则与 Hook 使用同一语义；路径规则仍大小写无关且接受 Windows 分隔符。（验证：运行 uv run pytest tests/unit/test_permissions_rules.py tests/unit/test_permissions_manager.py -q，观察旧用例不回归且新增用例通过）

- [ ] 配置边界：Hook YAML 缺失时启动为零规则；超时必须为有限正数；shell cwd 只能是工作区内相对路径；intercept 只能出现在同步的 tool.before shell 动作。（验证：运行 uv run pytest tests/unit/test_hooks_config.py -q -k "missing or control or cwd or intercept"，观察错误均在加载期出现）

## 生命周期与工具安全

- [ ] AC2：session.start、session.end、turn.start、turn.end、message.user、message.assistant、tool.before、tool.after、system.context_compacted 和 system.error 均有可观察触发点；每个事件只获得其允许字段。（验证：运行 uv run pytest tests/unit/test_hooks_runtime.py tests/unit/test_conversation.py tests/unit/test_app.py -q -k hook，观察事件顺序、字段和值正确）

- [ ] AC3：tool.before 根据工具名和结构化参数命中拦截时，真实工具业务代码的执行计数为零；结果为 permission_denied，带 hook_rule、hook_event、executed:false 和可行动的安全拒绝原因。（验证：运行 uv run pytest tests/unit/test_tools_executor.py -q -k "hook_before or rejection"，观察拦截结果写回但工具替身未执行）

- [ ] 权限优先：Hook 只在 JSON/Schema 校验和 PermissionManager 允许后运行；黑名单、工作区越界和权限 deny 不会被 Hook 绕过；Hook shell 同样经过权限判断。（验证：运行 uv run pytest tests/unit/test_tools_executor.py tests/unit/test_permissions_manager.py -q，观察预检失败不触发业务工具，危险命令不产生子进程）

- [ ] 工具后置：每个已开始执行的工具调用，无论成功、预期失败、超时或内部失败，都会触发一次 tool.after；JSON/Schema、权限或前置拦截失败不触发 tool.after，后置 Hook 也不能修改 ToolExecutionResult。（验证：运行 uv run pytest tests/unit/test_tools_executor.py -q -k "hook_after or timeout or internal"，观察触发次数和结果内容）

- [ ] 拦截原因脱敏：shell 守卫的标准输出、标准错误和内部正则不进入模型可见 ToolExecutionResult；模型只收到 deny_message 或通用拒绝原因。（验证：运行 uv run pytest tests/unit/test_hooks_actions.py tests/unit/test_tools_executor.py -q -k "intercept or redact"，观察模型结果不包含命令输出）

## 动作与执行控制

- [ ] AC5：shell Hook 将脱敏事件 JSON 写到标准输入并记录退出码、输出和超时；非拦截 shell 失败只记日志且不终止 Agent 主流程。（验证：运行 uv run pytest tests/unit/test_hooks_actions.py -q -k shell，观察成功、非零退出和超时用例均不抛出到调用方）

- [ ] AC6：prompt Hook 的 next_request、turn、session 三种作用域按约定进入后续 ProviderRequest；自动压缩预构建不会提前消费 next_request；提示词不生成 ChatMessage，也不破坏 assistant tool_calls 与 tool result 配对。（验证：运行 uv run pytest tests/unit/test_hooks_runtime.py tests/unit/test_conversation.py -q -k "instruction or prompt or request"，观察动态系统指令和会话历史）

- [ ] AC7：HTTP Hook 传递配置的方法、URL、请求头和 JSON 或文本 body；2xx、非 2xx、传输异常和超时均被记录，后者不停止 Agent。（验证：运行 uv run pytest tests/unit/test_hooks_actions.py -q -k http，观察 MockTransport 断言和失败隔离用例通过，且测试不访问真实外网）

- [ ] AC8：subagent 配置的 task 和 profile 经严格校验后可被列表展示；命中时只记录“等待 SubAgent 阶段对接”，不启动线程、Provider 或真实子 Agent。（验证：运行 uv run pytest tests/unit/test_hooks_actions.py tests/unit/test_hooks_config.py -q -k subagent，观察占位结果且无外部副作用）

- [ ] AC9：once Hook 在当前进程首个命中时执行，后续命中只记录跳过；后台 Hook 不阻塞发起事件；拦截动作配置 background:true 必须加载失败。（验证：运行 uv run pytest tests/unit/test_hooks_runtime.py tests/unit/test_hooks_config.py -q -k "once or background or close or intercept"，观察一次性状态、后台任务追踪和非法配置）

- [ ] Hook 失败隔离：配置成功加载后的 shell、HTTP、模板处理、后台任务或日志写入失败，都只形成 Hook 日志或测试可观察记录；除 HookInterception 外，不得改变会话提交、工具结果、上下文压缩或退出码。（验证：运行 uv run pytest tests/unit/test_hooks_runtime.py tests/unit/test_hooks_actions.py tests/unit/test_conversation.py -q -k "failure or error or isolation"，观察原主流程仍完成）

- [ ] 后台清理：OkCode 退出前取消并收尾仍在运行的后台 Hook，不遗留未处理任务警告，且清理失败不覆盖原退出码。（验证：运行 uv run pytest tests/unit/test_hooks_runtime.py tests/unit/test_cli.py -q -k "close or cleanup or hook"，观察任务取消记录和正常退出）

## 命令与终端

- [ ] AC10：/hooks 在未配置或空配置时显示明确空状态和配置路径；有规则时展示标识、事件、条件、动作类型、enabled、once、background、超时与 SubAgent 占位状态。（验证：运行 uv run pytest tests/unit/test_hooks_commands.py tests/unit/test_terminal.py -q -k hooks，观察表格和去除 ANSI 后的可见文本）

- [ ] /hooks 不调用 Provider、不改变 ChatMessage 历史，也不隐式重新加载正在运行的规则；它只读取当前验证成功的 HookRuntime 快照。（验证：运行 uv run pytest tests/unit/test_hooks_commands.py tests/unit/test_commands_handlers.py -q，观察 Provider 调用数与消息数量不变）

- [ ] 应用生命周期：欢迎界面前执行 session.start；EOF、/exit、权限确认中的 /exit、ProviderError 和一般运行时错误都执行一次 session.end；错误 Hook 只收到脱敏分类和摘要。（验证：运行 uv run pytest tests/unit/test_app.py -q -k "hook or exit or error"，观察 UI 原有语义及事件次数）

## 集成与回归

- [ ] AC11：不配置 Hook 时，工具调用、权限确认、上下文压缩、Skill、会话存档和 Provider 序列化保持原有行为；Hook 配置错误在 CLI 启动阶段以配置错误退出，不产生半初始化 App。（验证：运行 uv run pytest tests/unit/test_cli.py tests/unit/test_conversation.py tests/unit/test_tools_executor.py -q，观察无 Hook 用例均通过）

- [ ] CLI 装配：同一个 HookRuntime 实例同时传入 ToolExecutor、ConversationSession 和 OkCodeApp；配置缺失可启动，配置非法返回退出码 2，正常退出时在关闭 Provider 前调用 HookRuntime.aclose。（验证：运行 uv run pytest tests/unit/test_cli.py -q -k hook，观察构造和清理顺序）

- [ ] 端到端场景 1：临时工作区提供一个按工具参数拦截写入的 tool.before shell 守卫和一个 tool.after prompt Hook；模型先请求被拒绝工具，读取失败结果后改用允许工具并给出最终回答。（验证：运行 uv run pytest tests/integration/test_hook_lifecycle.py -q，观察被拒绝工具零副作用、后续调用成功、最终回答出现）

- [ ] 端到端场景 2：上述允许工具成功后，tool.after 的提示词注入出现在下一次 ProviderRequest；请求历史中的 assistant 工具调用与 tool result 仍成对且 ID 匹配。（验证：运行 uv run pytest tests/integration/test_hook_lifecycle.py -q，观察动态系统指令、调用顺序和协议配对断言）

- [ ] 编译与静态检查：新增代码在 Python 3.12 环境可导入，完整测试、格式检查、lint 和 Git 空白检查均通过。（验证：依次运行 uv run pytest -q、uv run ruff format --check .、uv run ruff check .、git diff --check，期望四条命令全部退出码为 0）
