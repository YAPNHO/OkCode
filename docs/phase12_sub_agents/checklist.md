# OkCode 第十二阶段：子 Agent 委派执行 Checklist

> 每一项都通过运行测试、观察命令输出或检查模型请求替身来验证，聚焦系统行为，不依赖真实模型服务或真实外网。

## 实现完整性

- [ ] agents 子系统模型已实现，AgentLaunchKind、AgentExecutionMode、AgentTaskStatus、AgentRole、AgentLaunchRequest、AgentTaskResult、AgentUsage 等结构可被导入和实例化（验证：运行 uv run pytest tests/unit/test_agents_models.py -q，期望通过）
- [ ] Agent 工具以固定名称 agent 注册，新增、删除或覆盖角色文件不会改变主 Agent 工具列表结构（验证：运行 uv run pytest tests/unit/test_agents_tool.py -q，观察工具定义稳定性测试通过）
- [ ] Agent 工具参数能区分 defined 和 fork 两条路径，defined 要求角色输入，fork 强制后台返回任务标识（验证：运行 uv run pytest tests/unit/test_agents_tool.py tests/unit/test_agents_launcher.py -q，期望对应测试通过）
- [ ] 角色 Markdown + YAML frontmatter 能被加载为 AgentRole，正文作为角色系统提示保留（验证：运行 uv run pytest tests/unit/test_agents_roles.py -q，期望合法角色解析测试通过）
- [ ] 插件级、内置级、用户级、项目级角色按 project > user > builtin > plugin 覆盖，同名覆盖能展示最终来源和 shadowed 记录（验证：运行 uv run pytest tests/unit/test_agents_roles.py -q，期望多来源覆盖测试通过）
- [ ] 非法角色 frontmatter 会在加载阶段给出可定位错误，包含文件、角色或候选角色、字段和失败原因（验证：运行 uv run pytest tests/unit/test_agents_roles.py -q，期望非法 YAML、未知字段、非法模型、非法权限、max_turns 非正整数等测试通过）
- [ ] 内置角色 code-reviewer 和 researcher 可被默认加载，且正文是中文系统提示（验证：运行 uv run pytest tests/unit/test_agents_roles.py -q，期望内置角色加载测试通过）

## 定义式子 Agent

- [ ] defined 子 Agent 从空白消息历史启动，不继承父对话历史（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，观察 defined 初始 messages 为空且只追加任务消息）
- [ ] defined 子 Agent 的角色正文进入 ProviderRequest 的系统补充指令，不写入普通 ChatMessage 历史（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，期望角色提示注入测试通过）
- [ ] defined 子 Agent 在模型不再请求工具时自然完成，并返回最终文本、状态、轮次数、工具调用摘要和用量（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/unit/test_agents_tool.py -q，期望自然完成结果测试通过）
- [ ] defined 前台模式能在短任务完成时直接把子 Agent 结果作为 agent 工具结果返回给主 Agent（验证：运行 uv run pytest tests/integration/test_subagent_turn.py -q，期望定义式前台端到端测试通过）
- [ ] defined 后台模式立即返回 task_id 和当前状态，完成后进入通知队列（验证：运行 uv run pytest tests/unit/test_agents_manager.py tests/unit/test_agents_tool.py -q，期望后台启动测试通过）

## Fork 式子 Agent

- [ ] fork 子 Agent 使用父对话 messages 快照作为初始历史，并在末尾追加 fork 任务消息（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，期望 fork messages 前缀测试通过）
- [ ] fork 子 Agent 不引用父 messages 可变状态，父对话后续追加消息不会改变已创建的 fork 快照（验证：运行 uv run pytest tests/unit/test_conversation.py -q，期望父快照副本测试通过）
- [ ] fork 子 Agent 强制后台执行，agent 工具调用立即返回 task_id，不阻塞主 Agent 等待结果（验证：运行 uv run pytest tests/unit/test_agents_launcher.py tests/integration/test_subagent_turn.py -q，期望 fork 强制后台测试通过）
- [ ] fork 第一次模型请求保持父消息前缀顺序稳定，不在父历史中间插入破坏 prompt cache 的消息（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/integration/test_subagent_turn.py -q，观察 ProviderRequest.messages 前缀断言通过）
- [ ] fork 子 Agent 完成后只把摘要、状态、错误和用量通知回主对话，不注入内部完整历史（验证：运行 uv run pytest tests/integration/test_subagent_turn.py -q，期望 Fork 后台通知测试通过）

## 运行时隔离

- [ ] 每个子 Agent 拥有独立 ConversationSession，消息历史、轮次数、Token 计数和停止状态互不影响（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，期望多任务隔离测试通过）
- [ ] 子 Agent 独立 ContextManager 的工具结果外置或压缩不会改变父对话历史（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/integration/test_subagent_turn.py -q，期望上下文隔离测试通过）
- [ ] 子 Agent ProviderError、工具失败、取消或超时不会导致父 REPL 崩溃，父对话收到可行动错误说明（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/unit/test_agents_manager.py -q，期望失败隔离测试通过）
- [ ] 子 Agent 达到 max_turns 后停止为 INCOMPLETE，并返回已有观察结果而不是继续无限运行（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，期望最大轮次测试通过）
- [ ] 子 Agent 不直接向终端发起交互式追问，需要用户输入时以阻塞说明返回主对话（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，期望非交互阻塞测试通过）

## 权限与工具安全

- [ ] 子 Agent 权限模式支持 inherit、default、strict、allow，inherit 只继承模式值，不共享父会话权限状态（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，期望权限模式解析和隔离测试通过）
- [ ] 子 Agent 内的一次性允许、会话允许或拒绝记录不会写回父 PermissionManager（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，期望父权限状态不变）
- [ ] 后台任务遇到需要交互确认的高风险操作时返回权限阻塞或拒绝结果，不弹出交互 prompt（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/integration/test_subagent_turn.py -q，期望后台权限阻塞测试通过）
- [ ] 工具过滤按全局禁止、后台白名单、父工具集合、角色白名单、角色黑名单、嵌套深度顺序执行（验证：运行 uv run pytest tests/unit/test_agents_filtering.py -q，期望过滤顺序测试通过）
- [ ] 默认情况下子 Agent 不可调用 agent 工具，防止无限嵌套（验证：运行 uv run pytest tests/unit/test_agents_filtering.py tests/integration/test_subagent_turn.py -q，期望默认禁止嵌套测试通过）
- [ ] 全局禁止工具即使出现在角色白名单中也不可见（验证：运行 uv run pytest tests/unit/test_agents_filtering.py -q，期望全局禁止优先测试通过）
- [ ] 过滤结果为空时子 Agent 仍可执行纯文本任务，并在结果中说明没有可用工具（验证：运行 uv run pytest tests/unit/test_agents_filtering.py tests/unit/test_agents_tool.py -q，期望空工具纯文本测试通过）
- [ ] 被过滤或权限拒绝的工具调用以结构化失败结果进入子 Agent 历史，并最终能被主对话看到（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/integration/test_subagent_turn.py -q，期望拒绝可观测测试通过）

## 后台任务管理

- [ ] AgentTaskManager 能追踪 QUEUED、RUNNING、BACKGROUND、COMPLETED、FAILED、CANCELLED、TIMED_OUT、INCOMPLETE 状态（验证：运行 uv run pytest tests/unit/test_agents_manager.py -q，期望状态生命周期测试通过）
- [ ] 显式后台、超时自动切后台、手动切后台三种方式都能产生同一个可查询任务（验证：运行 uv run pytest tests/unit/test_agents_manager.py -q，期望三种后台入口测试通过）
- [ ] move_to_background 重复调用是幂等的，不创建重复任务（验证：运行 uv run pytest tests/unit/test_agents_manager.py -q，期望重复切后台测试通过）
- [ ] /tasks 能展示后台任务标识、类型、角色或 Fork 标识、状态、等待时长或开始时间、轮次数、工具调用数、用量和摘要（验证：运行 uv run pytest tests/unit/test_agents_commands.py tests/unit/test_terminal.py -q，期望任务列表展示测试通过）
- [ ] /tasks 在没有任务时显示明确空状态（验证：运行 uv run pytest tests/unit/test_agents_commands.py -q，期望空状态测试通过）
- [ ] /tasks cancel <task_id> 能取消运行中任务，任务状态变为 CANCELLED 并进入通知队列（验证：运行 uv run pytest tests/unit/test_agents_commands.py tests/unit/test_agents_manager.py -q，期望取消测试通过）
- [ ] 任务 timeout_seconds 到期后停止后续模型请求和工具调用，状态变为 TIMED_OUT，保留已完成工作摘要和用量（验证：运行 uv run pytest tests/unit/test_agents_manager.py tests/unit/test_agents_runner.py -q，期望超时测试通过）
- [ ] 后台事件循环关闭时不会遗留挂起线程或未等待任务（验证：运行 uv run pytest tests/unit/test_agents_manager.py -q，期望测试进程正常退出）

## 主对话通知与用量

- [ ] 后台子 Agent 完成后，主对话下一次模型请求前能收到系统补充指令形式的任务完成通知（验证：运行 uv run pytest tests/unit/test_conversation.py tests/integration/test_subagent_turn.py -q，期望通知注入测试通过）
- [ ] 任务通知不是 Role.USER 消息，也不会拆开 assistant tool_calls 与 tool result 的配对（验证：运行 uv run pytest tests/unit/test_conversation.py -q，期望消息配对测试通过）
- [ ] TerminalUI 能渲染任务完成、失败、取消、超时通知（验证：运行 uv run pytest tests/unit/test_terminal.py -q，期望可见文本包含状态、角色、摘要）
- [ ] 通知内容有长度边界，超长 final_text 只通过 full_result_ref 保留，主对话只接收摘要（验证：运行 uv run pytest tests/unit/test_agents_notifications.py -q，期望截断和引用测试通过）
- [ ] 每个子 Agent 独立统计 input_tokens、output_tokens、cache read/write tokens、模型请求数和工具调用数（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q，期望用量汇总测试通过）
- [ ] /status 或等价状态展示能区分父 Agent 自身用量和子 Agent 用量（验证：运行 uv run pytest tests/unit/test_conversation.py tests/unit/test_terminal.py -q，期望父子用量区分测试通过）

## Hook 与命令集成

- [ ] Hook action.type=subagent 命中时不再只记录占位跳过，而是走 AgentLauncher 真实启动路径（验证：运行 uv run pytest tests/unit/test_agents_hooks.py tests/unit/test_hooks_runtime.py -q，期望 Hook 启动测试通过）
- [ ] Hook subagent 的 profile 字段映射为角色名，task 字段映射为子任务（验证：运行 uv run pytest tests/unit/test_agents_hooks.py -q，期望字段映射测试通过）
- [ ] 非拦截 Hook 触发的子 Agent 默认进入后台，不阻塞主 Agent 主流程（验证：运行 uv run pytest tests/unit/test_agents_hooks.py tests/unit/test_hooks_runtime.py -q，期望后台 Hook 测试通过）
- [ ] AgentLauncher 不存在时，Hook subagent 保留旧的占位跳过行为，避免破坏无 agents 组装测试（验证：运行 uv run pytest tests/unit/test_hooks_runtime.py -q，期望兼容路径测试通过）
- [ ] Hook subagent 配置层只校验字段类型和未知字段，不强依赖角色目录加载顺序（验证：运行 uv run pytest tests/unit/test_agents_hooks.py -q，期望配置兼容测试通过）
- [ ] /tasks 命令与现有 /help、/status、/hooks、/skill 命令共存，不破坏命令补全和命令分发（验证：运行 uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_commands_dispatcher.py tests/unit/test_terminal.py -q，期望命令系统回归通过）

## 集成与回归

- [ ] CLI 启动时能加载 AgentRoleCatalog、AgentTaskManager、AgentLauncher，并注册 AgentTool（验证：运行 uv run pytest tests/unit/test_cli.py -q，期望 CLI 组装测试通过）
- [ ] 未配置角色文件时 OkCode 仍能启动，至少内置角色可用，普通对话行为不变（验证：运行 uv run pytest tests/unit/test_cli.py tests/unit/test_conversation.py -q，期望无项目角色配置回归通过）
- [ ] 未调用 agent 工具时，普通对话、计划模式、/do、权限系统、上下文压缩、Skill 激活和 Hooks 行为保持不变（验证：运行 uv run pytest tests/unit/test_conversation.py tests/unit/test_prompt_modes.py tests/unit/test_skills_activation.py tests/unit/test_hooks_runtime.py -q，期望既有测试通过）
- [ ] 定义式子 Agent 端到端流程可完成：主 Agent 调用 agent -> 子 Agent 执行 -> 主 Agent 收到工具结果 -> 父历史不包含子内部全量历史（验证：运行 uv run pytest tests/integration/test_subagent_turn.py -q，期望 defined 端到端测试通过）
- [ ] Fork 后台流程可完成：主 Agent 调用 fork -> 立即拿到 task_id -> 后台完成 -> 下一轮主请求收到通知 -> Fork 请求保留父消息前缀（验证：运行 uv run pytest tests/integration/test_subagent_turn.py -q，期望 fork 端到端测试通过）
- [ ] Hook subagent 端到端流程可完成：Hook 命中 -> 后台任务创建 -> /tasks 可见 -> 完成通知回到主对话（验证：运行 uv run pytest tests/unit/test_agents_hooks.py tests/integration/test_subagent_turn.py -q，期望 Hook 集成测试通过）
- [ ] 新增测试不依赖真实模型服务、真实外网或危险命令（验证：检查 tests/unit/test_agents_*.py 和 tests/integration/test_subagent_turn.py，确认使用假 Provider、假工具、假权限确认器）
- [ ] 完整测试集通过（验证：运行 uv run pytest -q，期望全部通过）
- [ ] Ruff 格式化和 lint 通过（验证：运行 uv run ruff format . 和 uv run ruff check .，期望无错误）
- [ ] Git 空白检查通过（验证：运行 git diff --check，期望无 trailing whitespace 或冲突标记）

## 端到端场景

- [ ] 场景 1：用户要求主 Agent 审查一个局部改动，主 Agent 调用 defined/code-reviewer 子 Agent，子 Agent 只使用 read_file/search_code 等允许工具，主 Agent 收到审查摘要和用量（验证：运行 uv run pytest tests/integration/test_subagent_turn.py -q，观察 defined 审查场景通过）
- [ ] 场景 2：用户要求“另开一个后台分支继续排查”，主 Agent 调用 fork 子 Agent，工具立即返回后台 task_id，用户继续主对话，下一轮看到后台完成通知（验证：运行 uv run pytest tests/integration/test_subagent_turn.py -q，观察 fork 后台场景通过）
- [ ] 场景 3：Hook 在 tool.after 事件触发 subagent 调研任务，主流程不等待，/tasks 能看到任务，完成后主对话收到通知（验证：运行 uv run pytest tests/unit/test_agents_hooks.py tests/integration/test_subagent_turn.py -q，观察 Hook 场景通过）
- [ ] 场景 4：子 Agent 试图调用 agent 工具继续嵌套，系统过滤掉该工具或返回结构化拒绝，任务不会无限递归（验证：运行 uv run pytest tests/unit/test_agents_filtering.py tests/integration/test_subagent_turn.py -q，观察防嵌套场景通过）
- [ ] 场景 5：后台子 Agent 触发需要用户确认的 side-effect 工具，系统不弹出交互 prompt，子 Agent 返回权限阻塞说明，主对话能看到失败原因（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/integration/test_subagent_turn.py -q，观察后台权限场景通过）

## 文档对齐

- [ ] spec.md 的 AC1-AC11 均能在本 checklist 中找到对应检查项（验证：人工对照 docs/phase12_sub_agents/spec.md 和 checklist.md）
- [ ] plan.md 中列出的 agents 模块、既有模块改造和技术决策均被 task.md 与 checklist.md 覆盖（验证：人工对照 docs/phase12_sub_agents/plan.md、task.md、checklist.md）
- [ ] task.md 的 T1-T30 每个任务至少有一个对应的可运行检查或集成检查（验证：人工对照 docs/phase12_sub_agents/task.md 和 checklist.md）

