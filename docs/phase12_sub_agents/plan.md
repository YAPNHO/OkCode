# OkCode 第十二阶段：子 Agent 委派执行 Plan

## 架构概览

本阶段新增 agents 子系统，负责角色加载、Agent 工具入口、子 Agent 运行、后台任务管理和结果通知。主 Agent 仍由现有 ConversationSession 驱动；子 Agent 尽量复用 ConversationSession 的 Agent Loop、ToolExecutor、ContextManager、HookRuntime 和 Provider 请求模型，但每个子 Agent 创建独立运行状态。

整体分为八个组件：

- 角色目录：从插件级、内置级、用户级、项目级扫描 Markdown 角色文件，解析 YAML frontmatter，按优先级覆盖同名角色，并提供来源诊断。
- Agent 工具：注册一个固定名称的工具，参数中用 kind 选择 defined 或 fork。工具定义不随角色数量变化。
- 启动器：把工具参数或 Hook 子 Agent 动作转换为标准启动请求，解析角色、模型、权限和后台策略。
- 工具过滤器：按全局禁止、后台白名单、父工具集合、角色白名单、角色黑名单和嵌套深度计算子 Agent 可见工具。
- 子 Agent 运行器：为每个任务构造隔离 ConversationSession，消费其 TurnEvent，汇总最终文本、状态、工具调用、错误和用量。
- 后台任务管理器：追踪任务生命周期，支持前台运行、显式后台、自动切后台、手动切后台、取消、超时和状态查询。
- 通知桥：将已完成后台任务转换为主对话可见的系统通知，不伪造用户消息，不写入子 Agent 全量历史。
- 命令和 Hook 对接：新增后台任务查询命令，并把 Hook 的 subagent 动作从占位日志升级为真实启动路径。

## 核心数据结构

### AgentLaunchKind

枚举，区分两种启动方式：

- DEFINED：定义式子 Agent，从角色和空白历史启动。
- FORK：Fork 式子 Agent，从父对话快照启动并强制后台。

### AgentExecutionMode

枚举，表示启动时的执行方式：

- FOREGROUND：前台等待短任务完成，完成后直接返回工具结果。
- BACKGROUND：立即返回任务标识，完成后异步通知。
- AUTO：先前台运行，超过阈值后切后台。

### AgentTaskStatus

枚举，覆盖任务生命周期：

- QUEUED：已创建，尚未开始。
- RUNNING：正在运行。
- BACKGROUND：已转入后台运行。
- COMPLETED：自然完成。
- FAILED：运行失败。
- CANCELLED：用户取消。
- TIMED_OUT：任务超时。
- INCOMPLETE：达到最大轮次或模型需要用户输入，未自然完成。

### AgentModelPolicy

数据结构：

- kind: inherit、haiku、sonnet、opus。
- resolved_model: 解析后的真实模型名，可为空。

角色只声明模型策略；启动时由 AgentModelResolver 根据当前 ProviderConfig 和配置映射解析为 ProviderRequest.model_override，无法解析时报配置错误。

### AgentPermissionPolicy

数据结构：

- kind: inherit、default、strict、allow。
- resolved_mode: PermissionMode。

inherit 使用父对话当前权限模式。子 Agent 会创建独立 PermissionManager 或独立会话规则容器，避免一次性允许、会话允许和拒绝状态写回父 Agent。

### AgentRole

数据结构：

- name: 角色名。
- description: 用途说明。
- source_kind: plugin、builtin、user、project。
- source_path: 角色文件路径。
- tool_allowlist: 允许工具名集合，可为空。
- tool_denylist: 禁止工具名集合，可为空。
- model_policy: AgentModelPolicy。
- max_turns: 正整数。
- permission_policy: AgentPermissionPolicy。
- system_prompt: Markdown 正文。

frontmatter 建议格式：

  name: code-reviewer
  description: 审查局部代码变更并返回风险点
  tools:
    allow: [read_file, find_files, search_code]
    deny: [run_command]
  model: inherit
  max_turns: 6
  permission: strict

### AgentRoleCatalog

数据结构：

- roles: 最终可用角色，按 name 索引。
- shadowed: 被高优先级覆盖的角色记录。
- diagnostics: 非致命诊断信息。

核心方法：

- load(paths: AgentRolePaths) -> AgentRoleCatalog
- get(name: str) -> AgentRole
- list_entries() -> tuple[AgentRoleListEntry, ...]

### AgentToolRequest

Agent 工具的参数模型：

- kind: defined 或 fork。
- task: 子 Agent 要执行的任务。
- role: 定义式子 Agent 的角色名；kind 为 defined 时必填。
- background: 是否显式后台，默认 false；kind 为 fork 时强制 true。
- timeout_seconds: 可选任务超时。
- max_turns: 可选单次覆盖，但不得超过角色或全局上限。

JSON Schema 使用固定字段和 oneOf 校验 defined/fork 差异，但工具名称和顶层字段保持稳定。

### AgentLaunchRequest

启动器内部标准请求：

- task_id: 稳定任务标识。
- kind: AgentLaunchKind。
- task: 任务文本。
- role: AgentRole 或 None。
- parent_session_id: 父会话标识。
- parent_messages: Fork 快照消息。
- parent_tool_names: 父 Agent 当前可见工具名集合。
- execution_mode: AgentExecutionMode。
- timeout_seconds: 任务超时。
- max_turns: 最大模型轮次。
- depth: 当前嵌套深度。
- trigger: tool 或 hook。

### AgentToolPolicy

工具过滤输入和结果：

- global_denied: 全局禁止工具名，默认包含 agent。
- background_allowed: 后台允许工具名集合，默认使用 read_only 工具集合。
- parent_allowed: 父 Agent 当前可见工具名集合。
- role_allowlist: 角色白名单。
- role_denylist: 角色黑名单。
- depth: 当前嵌套深度。
- max_depth: 最大嵌套深度，默认 0。
- visible_tool_names: 最终工具集合。
- denied_reasons: 每个被拒工具的原因。

### AgentTaskResult

任务完成结果：

- task_id、kind、role_name。
- status: AgentTaskStatus。
- final_text: 子 Agent 最终回答或阻塞说明。
- summary: 注入主对话的短摘要。
- full_result_ref: 完整结果引用，本阶段为进程内引用或受控临时文本引用。
- error: 可行动错误说明。
- rounds: 模型请求次数。
- tool_calls: 工具调用摘要。
- usage: AgentUsage。
- started_at、ended_at。

### AgentTaskSnapshot

任务查询展示模型：

- task_id、kind、role_name、status。
- created_at、started_at、ended_at。
- elapsed_seconds。
- rounds、tool_call_count、usage。
- summary、error。

### AgentUsage

用量模型：

- input_tokens。
- output_tokens。
- total_tokens。
- cache_read_tokens。
- cache_write_tokens。
- model_request_count。
- tool_call_count。

TokenUsageReported 事件由子 Agent 运行器消费并汇总。父对话状态展示新增 parent_usage 和 child_usage 的区分字段。

## 核心接口

### AgentTool

职责：实现 Tool 协议，作为主 Agent 调用子 Agent 的唯一模型可见入口。

接口：

- definition: ToolDefinition。
- execute(arguments) -> ToolOutput。

执行逻辑：

1. 解析 AgentToolRequest。
2. 调用 AgentLauncher 构造 AgentLaunchRequest。
3. defined + foreground 时等待短任务完成并返回最终结果。
4. background、auto 切后台或 fork 时返回任务标识和当前状态。
5. 所有失败转成 ToolFailure 或 ToolOutput，不抛出未处理异常给 REPL。

### AgentLauncher

职责：将工具参数或 Hook 动作转换为可运行任务。

接口：

- launch_from_tool(request: AgentToolRequest, parent: ParentAgentContext) -> ToolOutput。
- launch_from_hook(action: SubAgentHookAction, context: HookContext) -> AgentTaskSnapshot。
- build_launch_request(...) -> AgentLaunchRequest。

### AgentTaskManager

职责：任务生命周期和后台运行控制。

接口：

- start(request: AgentLaunchRequest) -> AgentTaskHandle。
- run_foreground(request: AgentLaunchRequest) -> AgentTaskResult。
- move_to_background(task_id: str) -> AgentTaskSnapshot。
- cancel(task_id: str) -> AgentTaskSnapshot。
- list_snapshots() -> tuple[AgentTaskSnapshot, ...]。
- get_snapshot(task_id: str) -> AgentTaskSnapshot。
- drain_notifications(parent_session_id: str) -> tuple[AgentTaskNotification, ...]。

### AgentRunner

职责：实际运行一个子 Agent 到完成、失败、取消、超时或达到轮次上限。

接口：

- run(request: AgentLaunchRequest, cancel_token: AgentCancelToken) -> AgentTaskResult。

实现要点：

- 为每个任务创建独立 ConversationSession。
- defined 使用空 initial_messages 和 RolePromptContextFactory。
- fork 使用 parent_messages 作为 initial_messages，并把 task 作为新的用户消息追加，尽量保留父对话前缀。
- 使用 FilteredToolRegistry 和独立 ToolExecutor。
- 使用独立 PermissionManager 状态、独立 ContextManager 和独立 token 计数。
- 消费子会话事件并只保留摘要、最终文本、工具调用摘要和用量。

### AgentBackgroundLoop

职责：让后台任务在同步终端 prompt 阻塞时仍能继续运行。

接口：

- start()。
- submit(coro) -> concurrent handle。
- cancel(task_id)。
- close(timeout_seconds)。

设计选择：后台任务使用独立 asyncio 事件循环线程。Provider 默认通过同一 ProviderConfig 创建同配置实例，避免异步客户端跨事件循环复用风险；如果后续 Provider 声明可跨 loop 复用，再由 ProviderPool 返回共享实例。

### AgentNotificationBridge

职责：将后台任务结果送回主对话。

接口：

- to_system_instruction(notification) -> SystemInstruction。
- to_turn_event(notification) -> AgentTaskNotice。
- append_to_parent_context(conversation, notification)。

通知进入下一次模型请求的 additional_system_instructions，不伪造 Role.USER 消息。终端可以同时渲染 AgentTaskNotice，方便用户看到任务完成。

## 模块设计

### src/okcode/agents/models.py

职责：保存 agents 子系统的枚举和 dataclass。该文件不依赖 CLI、终端或具体 Provider，便于测试。

包含：AgentLaunchKind、AgentExecutionMode、AgentTaskStatus、AgentModelPolicy、AgentPermissionPolicy、AgentRole、AgentRoleCatalog、AgentToolRequest、AgentLaunchRequest、AgentToolPolicy、AgentUsage、AgentTaskResult、AgentTaskSnapshot、AgentTaskNotification。

### src/okcode/agents/roles.py

职责：角色文件发现、Markdown frontmatter 解析、字段校验、来源覆盖和列表展示。

对外接口：AgentRolePaths.for_workspace(workspace_root)、load_agent_roles(paths)、AgentRoleCatalog。

加载顺序：plugin -> builtin -> user -> project。后加载覆盖先加载。shadowed 记录保留用于诊断。

### src/okcode/agents/filtering.py

职责：根据 AgentToolPolicy 计算子 Agent 可见工具。

对外接口：filter_agent_tools(registry, policy) -> FilteredToolRegistry。

FilteredToolRegistry 只暴露 get、definitions、definitions_by_names、definitions_by_safety，内部转发到父 ToolRegistry，但只允许 visible_tool_names。

### src/okcode/agents/runner.py

职责：构造并运行隔离子 Agent。

关键类：AgentRunner、ChildConversationFactory、RolePromptContextFactory、ForkPromptContextFactory。

RolePromptContextFactory 把角色正文作为系统级补充指令加入 PromptBuildContext.additional_system_instructions，并保留环境信息、工具列表和必要项目指令。ForkPromptContextFactory 优先复用父对话的普通运行时提示上下文，只追加 Fork 任务说明，减少缓存前缀变化。

### src/okcode/agents/manager.py

职责：后台任务管理、状态转换、结果保存、通知队列、取消和超时。

关键类：AgentTaskManager、AgentTaskHandle、AgentBackgroundLoop、AgentCancelToken。

已完成任务保留在进程内有界队列，默认保留最近 N 个；完整结果引用只在当前进程有效，本阶段不做跨会话持久化。

### src/okcode/agents/tool.py

职责：实现 AgentTool 并定义稳定 JSON Schema。

工具名使用 agent。描述强调它用于委派独立子任务，默认子 Agent 不可再调用 agent 工具。

### src/okcode/agents/launcher.py

职责：统一启动路径，供 AgentTool 和 HookActionRunner 复用。

关键类：AgentLauncher、ParentAgentContext。

ParentAgentContext 由 ConversationSession 暴露，包含 session_id、messages、runtime_mode、visible_tool_names、permission_mode、depth 和 model 配置。

### src/okcode/agents/notifications.py

职责：格式化后台任务完成通知、终端事件和模型可见系统补充说明。

通知文本必须有长度上限；超长 final_text 写入 full_result_ref，主对话只接收摘要和引用。

### src/okcode/agents/__init__.py

职责：导出 agents 子系统的稳定公共入口。

## 既有模块改造

### src/okcode/cli.py

启动阶段新增：

1. 加载 AgentRoleCatalog。
2. 创建 AgentBackgroundLoop 和 AgentTaskManager。
3. 创建 AgentLauncher。
4. 向 ToolRegistry 注册 AgentTool。
5. 将 AgentTaskManager 传给 ConversationSession、OkCodeApp 和 HookActionRunner。

注册顺序放在 Skill 工具注册之后、ConversationSession 创建之前，确保父 Agent 工具列表稳定包含 agent。

### src/okcode/conversation.py

新增能力：

- 构造参数 initial_messages，用于 Fork 子 Agent 种子历史。
- 构造参数 agent_task_manager，用于主对话接收后台通知。
- parent_agent_context(tools) 方法，供 AgentTool 获取父快照。
- agent_task_list_event() 方法，供命令展示后台任务。
- drain_agent_notifications() 方法，在每轮模型请求前把完成通知加入 additional_system_instructions。
- child usage 汇总字段，用于 /status 区分父用量和子用量。

主 Agent 原有 stream_user_message、stream_do_instruction、权限、Hook、上下文压缩语义保持不变。

### src/okcode/models.py

新增 TurnEvent：

- AgentTaskNotice：终端渲染后台任务状态变化。
- AgentTaskListEvent：命令展示后台任务列表。

CommandStatusSnapshot 增加 child_input_tokens 和 child_output_tokens，或新增 AgentUsageSnapshot，避免混淆父 Agent 自身用量。

### src/okcode/terminal.py

新增渲染：

- AgentTaskNotice：显示任务完成、失败、取消、超时和摘要。
- AgentTaskListEvent：显示任务表格。

输出保持中文，任务标识短格式展示，完整标识可复制。

### src/okcode/commands/models.py

CommandConversationPort 增加：

- agent_task_list_event() -> TurnEvent。
- cancel_agent_task(task_id: str) -> TurnEvent。
- background_agent_task(task_id: str) -> TurnEvent。

### src/okcode/commands/defaults.py 和 handlers.py

新增 /tasks 命令：

- /tasks：列出后台任务。
- /tasks cancel <task_id>：取消任务。
- /tasks background <task_id>：将正在前台等待的任务切后台；如果已后台则幂等返回当前状态。

### src/okcode/hooks/actions.py

HookActionRunner 接收 AgentLauncher。命中 SubAgentHookAction 时调用 launch_from_hook，不再记录占位跳过。非拦截 Hook 默认 background=true；Hook 只接收任务标识和状态，不阻塞主流程。

### src/okcode/hooks/models.py 和 config.py

兼容现有 SubAgentHookAction 字段 profile。实现层将 profile 映射为 AgentRole.name。后续如要支持 fork Hook，可扩展字段 kind，但本阶段保持现有 YAML 兼容，默认为 defined。

### tests

新增测试集中在：

- tests/unit/test_agents_roles.py
- tests/unit/test_agents_filtering.py
- tests/unit/test_agents_tool.py
- tests/unit/test_agents_manager.py
- tests/unit/test_agents_runner.py
- tests/unit/test_agents_hooks.py
- tests/integration/test_subagent_turn.py

所有测试使用假 Provider、假工具、假权限确认器和短超时，不访问真实网络。

## 模块交互

### 启动流程

1. cli.py 创建 Workspace、ToolRegistry、MCP、PermissionManager、HookRuntime、Provider 和 ContextManager。
2. cli.py 加载 AgentRoleCatalog。
3. cli.py 创建 AgentBackgroundLoop、AgentTaskManager、AgentLauncher。
4. cli.py 注册 AgentTool 到 ToolRegistry。
5. cli.py 创建 ConversationSession，并传入 AgentTaskManager。
6. OkCodeApp 进入 REPL；每轮输入前后尝试渲染已完成后台通知。

### 定义式前台子 Agent

1. 主 Agent 调用 agent 工具，kind=defined，role=某角色，background=false。
2. AgentTool 解析参数并请求 AgentLauncher。
3. AgentLauncher 从 AgentRoleCatalog 解析角色。
4. 工具过滤器计算可见工具。
5. AgentTaskManager 以前台模式调用 AgentRunner。
6. AgentRunner 创建空历史子 ConversationSession，运行到自然完成或停止。
7. AgentTool 返回 ToolOutput，主 Agent 在同一轮看到子 Agent 结果。

### 定义式后台子 Agent

1. 主 Agent 调用 agent 工具，kind=defined，background=true，或 AUTO 超过阈值。
2. AgentTaskManager 创建任务并提交 AgentBackgroundLoop。
3. AgentTool 立即返回任务标识、状态和查询提示。
4. 后台完成后，AgentTaskManager 写入通知队列。
5. ConversationSession 下一轮模型请求前把通知作为系统补充指令注入；TerminalUI 同步渲染 AgentTaskNotice。

### Fork 式子 Agent

1. 主 Agent 调用 agent 工具，kind=fork。
2. AgentLauncher 捕获父对话 messages、runtime_mode、当前可见工具集合和权限模式。
3. AgentTaskManager 强制后台运行。
4. AgentRunner 使用父 messages 作为 initial_messages，把 task 作为新的用户消息追加。
5. ForkPromptContextFactory 尽量保持父提示前缀和工具定义顺序稳定。
6. 完成通知回到主对话，但不注入 Fork 内部完整历史。

### Hook 触发子 Agent

1. HookRuntime 命中 SubAgentHookAction。
2. HookActionRunner 调用 AgentLauncher.launch_from_hook。
3. AgentLauncher 使用 profile 解析角色，默认后台执行。
4. Hook 只记录任务已启动；子 Agent 完成后走统一后台通知。

### 取消和超时

1. /tasks cancel <task_id> 调用 AgentTaskManager.cancel。
2. Manager 设置 AgentCancelToken，并向后台 loop 取消对应 asyncio task。
3. AgentRunner 停止后续模型请求和工具调用，汇总已产生事件。
4. 任务状态变为 CANCELLED 或 TIMED_OUT，进入通知队列。

## 文件组织

- src/okcode/agents/__init__.py：导出 AgentTool、AgentLauncher、AgentTaskManager、load_agent_roles。
- src/okcode/agents/models.py：领域模型。
- src/okcode/agents/roles.py：角色加载和校验。
- src/okcode/agents/filtering.py：工具过滤和 FilteredToolRegistry。
- src/okcode/agents/runner.py：子 Agent 运行器和子会话工厂。
- src/okcode/agents/manager.py：后台任务管理器和后台事件循环。
- src/okcode/agents/tool.py：Agent 工具实现和 JSON Schema。
- src/okcode/agents/launcher.py：工具与 Hook 的统一启动器。
- src/okcode/agents/notifications.py：通知格式化和主对话注入。
- src/okcode/agents/builtin_roles/*.md：内置角色。
- src/okcode/cli.py：组装 agents 子系统并注册工具。
- src/okcode/conversation.py：父上下文快照、通知注入、子用量汇总。
- src/okcode/models.py：新增任务事件和用量快照。
- src/okcode/terminal.py：渲染任务通知和任务列表。
- src/okcode/commands/*.py：新增 /tasks 命令。
- src/okcode/hooks/actions.py：Hook 子 Agent 动作真实启动。
- src/okcode/hooks/models.py、config.py：保持 profile 兼容，必要时补充诊断。
- tests/unit/test_agents_*.py：单元测试。
- tests/integration/test_subagent_turn.py：端到端子 Agent 流程测试。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Agent 工具名称 | agent | 名称短且表达稳定，不随角色变化，满足工具列表稳定要求。 |
| 子 Agent 循环 | 复用 ConversationSession | 避免复制工具循环、上下文压缩、Hook 和 Provider 流式协议，降低行为分叉风险。 |
| 运行隔离 | 每个子 Agent 独立 ConversationSession、PermissionManager 状态、ContextManager 和 usage 计数 | 满足消息、权限、缓存、Token 和错误状态隔离。 |
| 后台执行 | 独立 asyncio 事件循环线程 | 当前 REPL prompt 是同步阻塞；独立 loop 才能让后台任务在用户输入期间继续推进。 |
| Provider 复用 | 共享 ProviderConfig 和 ProviderPool，默认每任务同配置实例 | 避免异步客户端跨事件循环复用风险，同时保持同一模型配置和缓存策略。 |
| Fork 缓存策略 | 保留父 messages 前缀，只追加子任务用户消息 | 最大化提示缓存命中可能，不强行保证所有 Provider 命中。 |
| 定义式上下文 | 空历史 + 角色系统提示 + 显式任务输入 | 防止父对话污染定义式角色。 |
| 工具过滤顺序 | 全局禁止 -> 后台白名单 -> 父工具集合 -> 角色白名单 -> 角色黑名单 -> 嵌套深度 | 先做安全边界，再做角色约束，结果更容易解释。 |
| 嵌套策略 | 默认子 Agent 不可调用 agent 工具，max_depth 默认 0 | 防止无限嵌套和后台任务爆炸。 |
| 通知注入 | SystemInstruction + TurnEvent，不创建 Role.USER | 满足异步可见，同时不伪造用户消息、不破坏工具配对。 |
| Hook 对接 | HookActionRunner 复用 AgentLauncher | 保证 Hook 和工具启动路径的权限、过滤、后台状态一致。 |
| 结果保存 | 进程内有界结果引用 | 符合本阶段不做跨会话持久化的边界。 |

## Spec 覆盖关系

| Spec 项 | Plan 归属 |
|---------|-----------|
| F1 | AgentTool、AgentToolRequest、固定 JSON Schema |
| F2、F4、F5、F6 | AgentRole、AgentRoleCatalog、roles.py |
| F3、N4 | ForkPromptContextFactory、AgentLaunchRequest、Fork 流程 |
| F7 | AgentModelPolicy、max_turns、AgentRunner |
| F8、F9、F10 | ChildConversationFactory、独立 PermissionManager、独立 ContextManager |
| F11、F12 | AgentRunner 完成判定和状态汇总 |
| F13、N3 | AgentNotificationBridge、ConversationSession 通知注入 |
| F14、F15、F16、F20、F23 | AgentTaskManager、AgentBackgroundLoop、/tasks 命令 |
| F17、F18、F19、N2 | AgentToolPolicy、filtering.py、默认禁止嵌套 |
| F21 | AgentUsage、父子用量区分 |
| F22 | HookActionRunner 与 AgentLauncher 对接 |
| N1、N5、N6、N7、N8 | 既有模块兼容、事件记录、无网络测试、有界结果和失败隔离 |

## 风险与处理

- 后台 loop 与 Provider 客户端跨线程复用风险：默认每任务创建同配置 Provider 实例，只共享配置和工厂；如 Provider 后续声明线程安全再优化为共享实例。
- Fork 路径提示缓存无法强保证：实现只保证不主动破坏父消息前缀，并通过测试验证请求顺序；真实缓存命中以 Provider 返回用量为准。
- 子 Agent 通知进入主上下文的长度风险：通知摘要固定长度上限，完整结果只通过引用保留。
- 权限确认无法在后台交互：后台任务遇到需要用户确认的高风险操作时返回权限阻塞结果，不弹出交互 prompt。
- 复用 ConversationSession 时误写父历史风险：子会话必须只使用 initial_messages 副本，不能引用父 messages 可变状态；测试覆盖父历史不变。
