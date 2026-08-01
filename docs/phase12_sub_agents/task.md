# OkCode 第十二阶段：子 Agent 委派执行 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | src/okcode/agents/__init__.py | 导出 agents 子系统公共入口 |
| 新建 | src/okcode/agents/models.py | 子 Agent 枚举、请求、角色、任务、用量和通知模型 |
| 新建 | src/okcode/agents/roles.py | 角色文件发现、frontmatter 解析、校验和覆盖 |
| 新建 | src/okcode/agents/filtering.py | 多层工具过滤和 FilteredToolRegistry |
| 新建 | src/okcode/agents/manager.py | 后台任务管理、状态转换、通知队列、取消和超时 |
| 新建 | src/okcode/agents/runner.py | 隔离子 Agent 运行器、子会话工厂、事件汇总 |
| 新建 | src/okcode/agents/tool.py | 稳定 Agent 工具定义和参数解析 |
| 新建 | src/okcode/agents/launcher.py | 工具路径和 Hook 路径的统一启动器 |
| 新建 | src/okcode/agents/notifications.py | 后台任务通知格式化和主对话注入 |
| 新建 | src/okcode/agents/builtin_roles/code-reviewer.md | 内置代码审查角色 |
| 新建 | src/okcode/agents/builtin_roles/researcher.md | 内置搜索/调研角色 |
| 修改 | src/okcode/cli.py | 加载角色目录、创建任务管理器、注册 AgentTool、注入 Hook 启动器 |
| 修改 | src/okcode/conversation.py | 父上下文快照、初始历史、后台通知注入、子用量汇总 |
| 修改 | src/okcode/models.py | 新增任务通知事件、任务列表事件和子用量展示模型 |
| 修改 | src/okcode/terminal.py | 渲染任务状态、任务列表和子 Agent 用量 |
| 修改 | src/okcode/commands/models.py | 扩展 CommandConversationPort 的任务管理接口 |
| 修改 | src/okcode/commands/defaults.py | 注册 /tasks 命令 |
| 修改 | src/okcode/commands/handlers.py | 实现 /tasks 列表、取消和切后台处理 |
| 修改 | src/okcode/hooks/actions.py | Hook subagent 动作改为真实启动 |
| 修改 | src/okcode/hooks/models.py | 保持 SubAgentHookAction 兼容，必要时补充字段注释 |
| 修改 | src/okcode/hooks/config.py | 校验 Hook subagent 与角色字段的兼容输入 |
| 修改 | tests/fakes.py | 增加子 Agent 相关假 Provider、假工具或辅助对象 |
| 新建 | tests/unit/test_agents_models.py | 模型默认值和状态转换单测 |
| 新建 | tests/unit/test_agents_roles.py | 角色加载、frontmatter 校验、来源覆盖单测 |
| 新建 | tests/unit/test_agents_filtering.py | 工具过滤顺序、白黑名单、嵌套限制单测 |
| 新建 | tests/unit/test_agents_manager.py | 后台任务生命周期、通知、取消、超时单测 |
| 新建 | tests/unit/test_agents_runner.py | 子 Agent 跑到底、最大轮次、用量汇总、隔离单测 |
| 新建 | tests/unit/test_agents_tool.py | Agent 工具 schema、前台/后台返回、错误路径单测 |
| 新建 | tests/unit/test_agents_launcher.py | 工具启动和 Hook 启动统一路径单测 |
| 新建 | tests/unit/test_agents_notifications.py | 通知长度边界和系统指令格式单测 |
| 新建 | tests/unit/test_agents_commands.py | /tasks 命令展示、取消和切后台单测 |
| 新建 | tests/unit/test_agents_hooks.py | Hook subagent 真实后台启动单测 |
| 新建 | tests/integration/test_subagent_turn.py | 定义式和 Fork 式子 Agent 端到端集成测试 |

## T1：建立 agents 包和核心模型

**文件：** src/okcode/agents/__init__.py、src/okcode/agents/models.py、tests/unit/test_agents_models.py  
**依赖：** 无

**步骤：**
1. 新建 agents 包入口，并只导出后续稳定公共类型占位。
2. 在 models.py 定义 AgentLaunchKind、AgentExecutionMode、AgentTaskStatus。
3. 定义 AgentModelPolicy、AgentPermissionPolicy、AgentRole、AgentRoleCatalog、AgentToolRequest。
4. 定义 AgentLaunchRequest、AgentToolPolicy、AgentUsage、AgentTaskResult、AgentTaskSnapshot、AgentTaskNotification。
5. 为状态枚举和用量汇总添加最小单元测试，确认默认值和不可变数据结构行为稳定。

**验证：** 运行 uv run pytest tests/unit/test_agents_models.py -q，期望新增模型测试全部通过。

## T2：实现角色路径和 Markdown frontmatter 基础解析

**文件：** src/okcode/agents/roles.py、tests/unit/test_agents_roles.py  
**依赖：** T1

**步骤：**
1. 定义 AgentRolePaths，包含 plugin、builtin、user、project 四类路径来源。
2. 实现 Markdown frontmatter 切分，只接受文件开头的 YAML frontmatter。
3. 解析正文为 system_prompt，正文为空时报 ConfigError。
4. 解析 name、description、tools、model、max_turns、permission 字段。
5. 增加非法 YAML、缺少 name、正文为空、未知字段的测试。

**验证：** 运行 uv run pytest tests/unit/test_agents_roles.py -q，期望基础解析和错误定位测试通过。

## T3：补齐角色字段校验和模型/权限策略解析

**文件：** src/okcode/agents/roles.py、tests/unit/test_agents_roles.py  
**依赖：** T2

**步骤：**
1. 校验 tools.allow 和 tools.deny 都必须是字符串列表。
2. 校验 model 只允许 inherit、haiku、sonnet、opus。
3. 校验 permission 只允许 inherit、default、strict、allow。
4. 校验 max_turns 必须是正整数。
5. 对白名单和黑名单同名冲突给出包含文件、角色名、字段名的错误。
6. 增加每类非法 frontmatter 的断言。

**验证：** 运行 uv run pytest tests/unit/test_agents_roles.py -q，期望所有校验分支都覆盖并通过。

## T4：实现多来源加载、同名覆盖和角色列表诊断

**文件：** src/okcode/agents/roles.py、src/okcode/agents/builtin_roles/code-reviewer.md、src/okcode/agents/builtin_roles/researcher.md、tests/unit/test_agents_roles.py  
**依赖：** T3

**步骤：**
1. 实现 load_agent_roles(paths) 按 plugin -> builtin -> user -> project 顺序加载。
2. 同名角色由后加载来源覆盖前加载来源。
3. 在 AgentRoleCatalog.shadowed 中记录被覆盖角色的来源。
4. 增加 list_entries() 返回角色名、说明、来源和覆盖诊断。
5. 添加两个内置角色文件，正文使用中文系统提示。
6. 增加同名覆盖和内置角色可加载测试。

**验证：** 运行 uv run pytest tests/unit/test_agents_roles.py -q，期望多来源覆盖和内置角色测试通过。

## T5：实现 FilteredToolRegistry 和工具过滤策略

**文件：** src/okcode/agents/filtering.py、tests/unit/test_agents_filtering.py  
**依赖：** T1

**步骤：**
1. 定义 filter_agent_tools(registry, policy)。
2. 按全局禁止、后台白名单、父工具集合、角色白名单、角色黑名单、嵌套深度顺序过滤。
3. 实现 FilteredToolRegistry.get、has、definitions、definitions_by_names、definitions_by_safety。
4. 记录 denied_reasons，便于工具结果和诊断展示。
5. 默认把 agent 工具加入 global_denied，max_depth 默认 0。

**验证：** 运行 uv run pytest tests/unit/test_agents_filtering.py -q，期望过滤顺序和默认禁止嵌套测试通过。

## T6：补齐工具过滤边界测试

**文件：** tests/unit/test_agents_filtering.py  
**依赖：** T5

**步骤：**
1. 覆盖后台白名单只允许 read_only 工具的场景。
2. 覆盖父工具集合缺失某工具时角色白名单也不能重新放开的场景。
3. 覆盖角色黑名单优先于角色白名单最终结果的场景。
4. 覆盖过滤结果为空时仍返回可用空 registry 和诊断原因。
5. 覆盖 definitions_by_names 请求不可见工具时报错。

**验证：** 运行 uv run pytest tests/unit/test_agents_filtering.py -q，期望所有过滤边界测试通过。

## T7：实现后台任务管理器的同步生命周期

**文件：** src/okcode/agents/manager.py、tests/unit/test_agents_manager.py  
**依赖：** T1

**步骤：**
1. 定义 AgentTaskHandle、AgentCancelToken 和 AgentTaskManager。
2. 实现任务创建、QUEUED -> RUNNING -> COMPLETED/FAILED/INCOMPLETE 状态转换。
3. 实现 list_snapshots、get_snapshot 和有界完成结果保存。
4. 实现 drain_notifications(parent_session_id)，只返回对应父会话通知。
5. 增加同步假 runner 测试任务成功、失败和通知队列。

**验证：** 运行 uv run pytest tests/unit/test_agents_manager.py -q，期望同步生命周期测试通过。

## T8：实现后台事件循环、取消和超时

**文件：** src/okcode/agents/manager.py、tests/unit/test_agents_manager.py  
**依赖：** T7

**步骤：**
1. 实现 AgentBackgroundLoop，内部使用独立 asyncio 事件循环线程。
2. 实现 start、submit、cancel、close。
3. AgentTaskManager.start 支持后台提交任务。
4. 实现 cancel(task_id)，设置 cancel token 并取消底层 asyncio task。
5. 实现任务级 timeout_seconds，到期后标记 TIMED_OUT。
6. 测试后台任务会完成、取消任务进入 CANCELLED、超时任务进入 TIMED_OUT。

**验证：** 运行 uv run pytest tests/unit/test_agents_manager.py -q，期望后台、取消、超时测试通过且无悬挂线程。

## T9：实现任务前台、后台和自动切后台策略

**文件：** src/okcode/agents/manager.py、tests/unit/test_agents_manager.py  
**依赖：** T8

**步骤：**
1. AgentTaskManager 支持 run_foreground(request)。
2. 支持 AgentExecutionMode.BACKGROUND 立即返回任务快照。
3. 支持 AgentExecutionMode.AUTO 超过阈值后转后台。
4. move_to_background(task_id) 必须幂等。
5. Fork 请求在 manager 层强制 BACKGROUND。
6. 测试显式后台、自动切后台、手动切后台和重复切后台。

**验证：** 运行 uv run pytest tests/unit/test_agents_manager.py -q，期望三种进入后台方式全部通过。

## T10：实现通知格式化和长度边界

**文件：** src/okcode/agents/notifications.py、tests/unit/test_agents_notifications.py  
**依赖：** T7

**步骤：**
1. 实现 AgentNotificationBridge。
2. 将 AgentTaskResult 格式化为主对话可见的 SystemInstruction。
3. 将 AgentTaskResult 格式化为终端可渲染的任务通知事件载荷。
4. 对 summary、final_text 和 error 设置长度边界。
5. 超长完整结果只保留 full_result_ref。
6. 测试通知不是用户消息、内容含任务标识、超长结果被截断。

**验证：** 运行 uv run pytest tests/unit/test_agents_notifications.py -q，期望通知边界测试通过。

## T11：为 ConversationSession 支持初始历史和父上下文快照

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py  
**依赖：** T1

**步骤：**
1. ConversationSession 构造参数增加 initial_messages，默认空。
2. 初始化 _messages 时使用 initial_messages 的 tuple 副本。
3. 新增 parent_agent_context(tools) 或等价方法，返回父会话 id、messages、runtime_mode、权限模式和可见工具名。
4. 确认 reset_session 不影响已交给子 Agent 的历史快照。
5. 增加测试：Fork 快照是副本，父会话后续追加消息不改变快照。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q，期望既有会话测试和新增快照测试通过。

## T12：实现子 Agent 运行器的基础跑到底逻辑

**文件：** src/okcode/agents/runner.py、tests/unit/test_agents_runner.py  
**依赖：** T1、T5、T11

**步骤：**
1. 定义 AgentRunner.run(request, cancel_token)。
2. 创建独立 ConversationSession，传入子 registry、子 executor、子 context_manager 和子 prompt factory。
3. defined 路径使用空 initial_messages，并把 task 作为用户消息执行。
4. 消费 TurnEvent，汇总最终文本、轮次数、工具调用数和 TokenUsage。
5. 模型不再请求工具时返回 COMPLETED。
6. 测试无工具的定义式子 Agent 能自然完成。

**验证：** 运行 uv run pytest tests/unit/test_agents_runner.py -q，期望基础定义式运行测试通过。

## T13：实现最大轮次、停止原因和错误汇总

**文件：** src/okcode/agents/runner.py、tests/unit/test_agents_runner.py  
**依赖：** T12

**步骤：**
1. 将 AgentLaunchRequest.max_turns 映射到子 ConversationSession 的 AgentConfig.max_iterations 或独立循环限制。
2. 达到最大轮次时返回 INCOMPLETE，而不是父 REPL 异常。
3. ProviderError 转成 FAILED，并保留 safe message。
4. 权限拒绝或工具失败进入子历史后允许模型继续；如果模型无法继续，最终结果中保留阻塞说明。
5. 增加最大轮次、ProviderError、工具失败汇总测试。

**验证：** 运行 uv run pytest tests/unit/test_agents_runner.py -q，期望停止原因和失败汇总测试通过。

## T14：实现 defined 角色提示上下文

**文件：** src/okcode/agents/runner.py、tests/unit/test_agents_runner.py  
**依赖：** T12、T4

**步骤：**
1. 实现 RolePromptContextFactory。
2. 将 AgentRole.system_prompt 作为 SystemInstruction 注入子 Agent 生命周期。
3. 保留环境信息、工具列表和必要项目指令。
4. 确认角色正文不写入普通 ChatMessage 历史。
5. 测试 ProviderRequest.prompt.dynamic_system 中包含角色提示，messages 中不包含角色提示。

**验证：** 运行 uv run pytest tests/unit/test_agents_runner.py -q，期望角色提示注入测试通过。

## T15：实现 Fork 路径初始历史和缓存友好顺序

**文件：** src/okcode/agents/runner.py、tests/unit/test_agents_runner.py  
**依赖：** T11、T12

**步骤：**
1. Fork 路径使用 parent_messages 作为 initial_messages。
2. 将 fork task 作为新的用户消息追加执行。
3. 保持 parent_messages 原始顺序，不在中间插入动态消息。
4. PromptCachePolicy 继承父会话设置。
5. 测试 ProviderRequest.messages 前缀等于父消息快照，并在末尾追加 fork 任务消息。

**验证：** 运行 uv run pytest tests/unit/test_agents_runner.py -q，期望 Fork 消息顺序和 cache policy 测试通过。

## T16：实现子 Agent 权限隔离

**文件：** src/okcode/agents/runner.py、src/okcode/agents/launcher.py、tests/unit/test_agents_runner.py  
**依赖：** T12、T13

**步骤：**
1. 为每个子 Agent 创建独立 PermissionManager 状态，复用规则集和工作区。
2. inherit 权限模式使用父权限模式的值，但不共享 session rules。
3. 后台任务遇到需要交互确认的权限请求时返回阻塞/拒绝结果，不调用终端 prompt。
4. 子 Agent 一次性允许或会话允许不写回父 PermissionManager。
5. 测试子 Agent 权限状态变化后父权限状态不变。

**验证：** 运行 uv run pytest tests/unit/test_agents_runner.py -q，期望权限隔离测试通过。

## T17：实现 AgentTool 固定 schema 和参数解析

**文件：** src/okcode/agents/tool.py、tests/unit/test_agents_tool.py  
**依赖：** T1

**步骤：**
1. 实现 AgentTool.definition，工具名固定为 agent。
2. JSON Schema 顶层字段固定：kind、task、role、background、timeout_seconds、max_turns。
3. kind=defined 时要求 role；kind=fork 时忽略或拒绝 role，按 spec 选择明确行为。
4. task 必须是非空字符串。
5. 参数解析失败转为 ToolFailure，错误可被模型理解。
6. 测试角色数量变化不改变 tool definition。

**验证：** 运行 uv run pytest tests/unit/test_agents_tool.py -q，期望 schema 和解析测试通过。

## T18：实现 AgentLauncher 的工具启动路径

**文件：** src/okcode/agents/launcher.py、tests/unit/test_agents_launcher.py  
**依赖：** T4、T5、T9、T17

**步骤：**
1. 定义 ParentAgentContext。
2. 实现 launch_from_tool，将 AgentToolRequest 转换为 AgentLaunchRequest。
3. defined 路径解析 AgentRole。
4. fork 路径读取 parent messages 和 parent visible tools，并强制 background。
5. 调用工具过滤器生成 visible_tool_names 和 denied_reasons。
6. 调用 AgentTaskManager 执行前台或后台任务。
7. 测试 defined 前台、defined 后台、fork 强制后台和未知角色错误。

**验证：** 运行 uv run pytest tests/unit/test_agents_launcher.py -q，期望工具启动路径测试通过。

## T19：补齐 AgentTool 执行结果格式

**文件：** src/okcode/agents/tool.py、tests/unit/test_agents_tool.py  
**依赖：** T18

**步骤：**
1. 前台完成时返回包含 status、summary、final_text、usage 的 ToolOutput。
2. 后台启动时返回 task_id、status、role/kind 和查询提示。
3. 过滤后无可用工具时允许纯文本运行，并在返回中说明。
4. 启动失败时返回结构化 ToolFailure。
5. 测试前台完成、后台启动、无工具纯文本和启动失败四类返回。

**验证：** 运行 uv run pytest tests/unit/test_agents_tool.py -q，期望执行结果格式测试通过。

## T20：在 CLI 中组装 agents 子系统并注册 AgentTool

**文件：** src/okcode/cli.py、tests/unit/test_cli.py  
**依赖：** T4、T8、T18、T19

**步骤：**
1. 在 CLI 启动阶段加载 AgentRoleCatalog。
2. 创建 AgentBackgroundLoop、AgentTaskManager、AgentLauncher。
3. 注册 AgentTool 到 ToolRegistry，确保父 Agent 可见工具包含 agent。
4. 将 AgentTaskManager 传入 ConversationSession 和 OkCodeApp。
5. 将 AgentLauncher 传入 HookActionRunner。
6. 增加 CLI 组装测试，确认无角色配置时仍可启动并注册 agent 工具。

**验证：** 运行 uv run pytest tests/unit/test_cli.py -q，期望 CLI 组装测试通过。

## T21：把后台通知注入主 ConversationSession

**文件：** src/okcode/conversation.py、src/okcode/agents/notifications.py、tests/unit/test_conversation.py  
**依赖：** T10、T20

**步骤：**
1. ConversationSession 构造参数接收 AgentTaskManager。
2. 每次构建 ProviderRequest 前 drain 当前父 session 的完成通知。
3. 把通知转换为 additional_system_instructions。
4. 同步产生 AgentTaskNotice 事件供终端渲染。
5. 确认通知不写入 Role.USER 消息，不破坏 tool_calls/tool_results 配对。
6. 测试后台完成通知进入下一次模型请求且不污染消息历史。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py tests/unit/test_agents_notifications.py -q，期望通知注入测试通过。

## T22：实现父子用量区分

**文件：** src/okcode/conversation.py、src/okcode/models.py、src/okcode/terminal.py、tests/unit/test_conversation.py、tests/unit/test_terminal.py  
**依赖：** T12、T21

**步骤：**
1. 在 AgentTaskManager 完成任务时暴露 AgentUsage 汇总。
2. ConversationSession 累加 child_input_tokens、child_output_tokens 和 child tool calls。
3. status_snapshot 或新增 usage snapshot 区分父 Agent 自身用量和子 Agent 用量。
4. TerminalUI 展示状态时保持现有字段兼容，并新增子 Agent 用量。
5. 测试父用量和子用量不会混在同一计数里。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py tests/unit/test_terminal.py -q，期望用量区分测试通过。

## T23：新增任务查询和控制命令

**文件：** src/okcode/commands/models.py、src/okcode/commands/defaults.py、src/okcode/commands/handlers.py、tests/unit/test_agents_commands.py、tests/unit/test_commands_handlers.py  
**依赖：** T7、T9、T10

**步骤：**
1. CommandConversationPort 增加 agent_task_list_event、cancel_agent_task、background_agent_task。
2. 在 defaults.py 注册 /tasks 命令。
3. handlers.py 实现 /tasks、/tasks cancel <task_id>、/tasks background <task_id>。
4. 无任务时返回明确空状态。
5. 未知 task_id 返回错误通知。
6. 增加命令解析、列表、取消、切后台和空状态测试。

**验证：** 运行 uv run pytest tests/unit/test_agents_commands.py tests/unit/test_commands_handlers.py -q，期望命令测试通过。

## T24：新增终端任务事件渲染

**文件：** src/okcode/models.py、src/okcode/terminal.py、tests/unit/test_terminal.py  
**依赖：** T10、T23

**步骤：**
1. 在 models.py 新增 AgentTaskNotice 和 AgentTaskListEvent。
2. TerminalUI.render_event 支持任务完成、失败、取消、超时通知。
3. TerminalUI.render_event 支持任务列表表格。
4. 任务标识显示短格式，同时保留可复制完整 id。
5. 测试 ANSI 清理后的可见文本包含状态、角色、用量和摘要。

**验证：** 运行 uv run pytest tests/unit/test_terminal.py -q，期望终端渲染测试通过。

## T25：Hook subagent 动作接入真实启动器

**文件：** src/okcode/hooks/actions.py、src/okcode/hooks/models.py、src/okcode/hooks/config.py、tests/unit/test_agents_hooks.py、tests/unit/test_hooks_runtime.py  
**依赖：** T18、T20

**步骤：**
1. HookActionRunner 构造参数增加可选 AgentLauncher。
2. 命中 SubAgentHookAction 时调用 launch_from_hook。
3. profile 字段映射为 role，task 字段映射为子任务。
4. 非拦截 Hook 默认后台运行，不阻塞主流程。
5. AgentLauncher 不存在时保留旧的占位跳过行为，方便测试和兼容。
6. 测试 Hook subagent 能启动后台任务，旧占位路径仍可工作。

**验证：** 运行 uv run pytest tests/unit/test_agents_hooks.py tests/unit/test_hooks_runtime.py -q，期望 Hook 对接测试通过。

## T26：补充 Hook 配置对角色字段的校验

**文件：** src/okcode/hooks/config.py、tests/unit/test_agents_hooks.py  
**依赖：** T25

**步骤：**
1. 保持 action.type=subagent、task、profile 现有字段兼容。
2. profile 为空时允许由默认角色策略处理，或在 AgentLauncher 阶段报未知角色。
3. 配置层只校验字段类型，不强依赖角色目录，避免启动顺序耦合。
4. 增加 profile 类型错误、task 缺失、未知字段测试。

**验证：** 运行 uv run pytest tests/unit/test_agents_hooks.py -q，期望配置兼容测试通过。

## T27：新增定义式子 Agent 端到端集成测试

**文件：** tests/integration/test_subagent_turn.py  
**依赖：** T12、T14、T18、T19、T20、T21

**步骤：**
1. 使用假 Provider 构造主 Agent 调用 agent 工具的流式响应。
2. 子 Agent 使用独立假 Provider 返回最终文本。
3. 验证主 Agent 工具结果包含子 Agent 摘要和用量。
4. 验证父对话消息不包含子 Agent 内部完整历史。
5. 验证工具调用与工具结果配对完整。

**验证：** 运行 uv run pytest tests/integration/test_subagent_turn.py -q，期望定义式端到端测试通过。

## T28：新增 Fork 后台和异步通知集成测试

**文件：** tests/integration/test_subagent_turn.py  
**依赖：** T15、T18、T21、T24

**步骤：**
1. 主 Agent 调用 kind=fork。
2. 验证 agent 工具立即返回后台 task_id。
3. 等待假后台任务完成。
4. 触发下一轮主 Agent 请求，验证通知进入 ProviderRequest 的系统补充指令。
5. 验证 Fork 子请求 messages 前缀等于父历史快照。
6. 验证终端可渲染任务完成事件。

**验证：** 运行 uv run pytest tests/integration/test_subagent_turn.py -q，期望 Fork 后台通知测试通过。

## T29：覆盖安全和隔离集成场景

**文件：** tests/integration/test_subagent_turn.py、tests/unit/test_agents_runner.py、tests/unit/test_agents_filtering.py  
**依赖：** T16、T18、T21

**步骤：**
1. 验证子 Agent 默认不可调用 agent 工具。
2. 验证后台任务不可执行需要交互确认的高风险操作。
3. 验证子 Agent 权限 session allow 不影响父 Agent。
4. 验证子 Agent context compaction 或失败不改变父历史。
5. 验证全局禁止工具即使在角色白名单中也不可见。

**验证：** 运行 uv run pytest tests/unit/test_agents_runner.py tests/unit/test_agents_filtering.py tests/integration/test_subagent_turn.py -q，期望安全隔离场景通过。

## T30：更新全量回归并修正静态检查问题

**文件：** 所有本阶段修改文件  
**依赖：** T1-T29

**步骤：**
1. 运行 focused agents 测试，修复失败。
2. 运行完整单元和集成测试，修复回归。
3. 运行 Ruff 格式化和 lint。
4. 运行 git diff --check，修复空白和换行问题。
5. 确认 docs/phase12_sub_agents/spec.md、plan.md、task.md 与实现没有明显矛盾。

**验证：** 依次运行 uv run pytest -q、uv run ruff format .、uv run ruff check .、git diff --check，期望全部通过。

## 执行顺序

1. T1 -> T2 -> T3 -> T4
2. T5 -> T6
3. T7 -> T8 -> T9 -> T10
4. T11 -> T12 -> T13 -> T14 -> T15 -> T16
5. T17 -> T18 -> T19 -> T20
6. T21 -> T22 -> T23 -> T24
7. T25 -> T26
8. T27 -> T28 -> T29 -> T30

## 依赖说明

- T1 是所有 agents 子系统模型基础。
- T4 完成后，定义式角色相关任务才能开始。
- T5/T6 完成后，runner 和 launcher 才能使用受限工具集合。
- T7-T10 完成后，后台相关任务才能接入。
- T11 是 Fork 和通知注入的 ConversationSession 基础。
- T12-T16 形成可运行的子 Agent 核心。
- T17-T20 让主 Agent 能通过工具真正启动子 Agent。
- T21-T24 让后台任务对主对话和用户可见。
- T25-T26 完成 Hook subagent 占位到真实运行的升级。
- T27-T30 是端到端验收和全量回归。

