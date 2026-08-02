# OkCode 第九阶段：命令注册与分发机制 Plan

## 架构概览

本阶段新增独立的 okcode.commands 包，负责命令元数据、解析、注册冲突检测、补全候选和内置命令处理。命令层不直接调用 Rich Console 或 prompt-toolkit，而是通过命令结果、会话端口和界面动作表达行为。

交互入口调整为“应用层统一分流”。OkCodeApp.run() 读取用户输入后，先交给 CommandDispatcher。如果不是命令，则调用 ConversationSession 的普通用户消息入口；如果是命令，则执行命令结果，包括本地事件、界面动作、退出动作、会话恢复动作、会话清理动作，或把固定提示词/既有执行指令注入对话并立即触发回合。

ConversationSession 保留 Agent Loop、上下文压缩、权限系统、会话恢复、长期记忆和历史原子提交职责。它实现命令层需要的窄接口：手动压缩、当前运行时模式读写、会话状态快照、记忆文件快照、恢复会话、清理并开启新会话、按指定模式执行普通用户消息、执行既有 /do 行为。

TerminalUI 继续负责输入和渲染。它新增命令补全器、底部状态栏、清屏和命令事件渲染能力。状态栏由运行时模式驱动，显示 [DEFAULT] 或 [PLAN]。

本阶段把“运行时模式”建模为 DEFAULT 与 PLAN。它控制后续回合使用全量工具还是只读工具，以及是否注入计划模式提示。它不替代现有工具权限系统的规则加载、黑名单和确认流程。

## 核心数据结构

### CommandKind

定义在 okcode.commands.models 中，取值为 LOCAL、UI、PROMPT。

- LOCAL：纯本地类，只读取或展示本地状态，不调用 Provider，不写入普通对话历史。
- UI：影响界面类，改变 TUI 生命周期、当前会话边界、运行时模式、状态栏或可见界面。
- PROMPT：提示词类，把固定文本或既有执行指令注入对话，并立即触发普通回合。

### RuntimeMode

定义在 okcode.commands.models 中，取值为 DEFAULT、PLAN。

- DEFAULT：状态栏显示 [DEFAULT]，普通输入使用默认工具范围。
- PLAN：状态栏显示 [PLAN]，普通输入使用只读工具和计划模式提示。

### ParsedCommand

字段：

- raw: str，完整原始输入。
- name: str，去掉前导斜杠且小写归一后的命令名。
- args: str，第一个空格之后的参数正文，去掉首尾空白但不重新拆词。

### CommandDefinition

字段：

- name: str
- aliases: tuple[str, ...]
- description: str
- usage: str
- kind: CommandKind
- argument_hint: str | None
- hidden: bool
- handler: CommandHandler

注册中心只接受不带斜杠的 name 和 aliases。构造时统一归一为小写，并检查命令名、别名、别名与命令名之间的冲突。

### CommandUiAction

取值为 NONE、CLEAR_SCREEN、EXIT、SELECT_SESSION、RESET_SESSION。

- CLEAR_SCREEN：清理可见终端输出。
- EXIT：结束 TUI 进程。
- SELECT_SESSION：打开历史会话列表。
- RESET_SESSION：关闭当前会话存档并开启新会话，配合 ConversationSession.reset_session() 使用。

### ForwardedUserMessage

字段：

- content: str，最终发送给 Agent 的用户任务文本。
- runtime_mode: RuntimeMode，本次任务使用的模式。
- tool_scope: ToolScope，取值 CURRENT_MODE、ALL、READ_ONLY。
- preset_name: str | None，提示词命令名称，用于测试观察。

### CommandResult

字段：

- events: tuple[TurnEvent, ...]，立即渲染的本地事件。
- stream: AsyncIterator[TurnEvent] | None，命令产生的异步事件流，例如手动压缩。
- ui_action: CommandUiAction，界面或生命周期动作。
- forward: ForwardedUserMessage | None，需要交给 Agent 的用户任务。

### CommandContext

字段：

- config: ProviderConfig，当前 Provider 配置。
- registry: CommandRegistry，命令注册中心。
- conversation: CommandConversationPort，会话能力端口。
- workspace_root: Path，当前工作目录。

命令处理通过该上下文访问注册元数据和会话能力，不直接操作终端实现。

### CommandConversationPort

由 ConversationSession 实现，提供以下能力：

- runtime_mode 属性：读取当前 DEFAULT 或 PLAN。
- set_runtime_mode(mode)：切换运行时模式。
- status_snapshot()：返回 /status 所需六项信息。
- memory_snapshot()：返回项目层与用户层记忆文件名列表。
- permission_string()：返回与 /status 一致的运行时模式字符串。
- session_snapshot()：返回当前 session 标识和存档文件路径。
- list_resumable_sessions()：列出可恢复会话。
- restore_session(session_id)：从目标会话最后一个 compact 标记之后恢复。
- stream_manual_compaction()：执行既有手动压缩事件流。
- reset_session()：关闭当前会话存档，创建新 session 标识与存档文件，清空内存消息，归零累计 Token 与回合数。
- stream_user_message(text, mode=None, tool_scope=None)：按指定模式执行普通用户消息。
- stream_do_instruction()：执行本 spec 实施前的 /do 外部行为。

### CommandStatusSnapshot

字段：

- permission_mode: str，当前运行时模式字符串。
- cumulative_input_tokens: int
- cumulative_output_tokens: int
- available_tool_count: int
- loaded_memory_item_count: int
- model_name: str
- working_directory: str

/status 只展示这六项信息。/permission 输出的字符串必须与 permission_mode 字段一致。

### CommandMemorySnapshot

字段：

- project_memory_files: tuple[str, ...]
- user_memory_files: tuple[str, ...]

仅包含文件名，不包含路径和文件内容。

### CommandSessionSnapshot

字段：

- session_id: str
- journal_path: str

/session 至少展示这两个字段，后续可以展示更多只读标识信息。

### CompletionCandidate

字段：

- text: str，补全要插入的命令文本。
- display: str，候选菜单展示名。
- description: str，候选说明。

注册中心根据可见命令生成候选。隐藏命令可解析但不会返回候选。

## 模块设计

### okcode.commands.models

**职责：** 定义命令相关枚举、数据结构、端口协议和结果类型。

**对外接口：** CommandKind、RuntimeMode、ParsedCommand、CommandDefinition、CommandResult、ForwardedUserMessage、CommandConversationPort、CommandStatusSnapshot、CommandMemorySnapshot、CommandSessionSnapshot。

**依赖：** okcode.models、okcode.sessions、typing.Protocol。

### okcode.commands.registry

**职责：** 管理命令注册、别名索引、冲突检测、帮助排序和补全候选。

**对外接口：**

- CommandRegistry(commands)
- resolve(name) -> CommandDefinition | None
- visible_commands() -> tuple[CommandDefinition, ...]，按命令名字典序返回。
- completion_candidates(prefix) -> tuple[CompletionCandidate, ...]

**依赖：** okcode.commands.models。

### okcode.commands.parser

**职责：** 识别斜杠输入并解析命令名和参数。

**对外接口：**

- ParseResult(empty, is_command, command, text)
- CommandParser.parse(text) -> ParseResult

**依赖：** 无业务依赖。

### okcode.commands.handlers

**职责：** 实现十二条内置命令处理函数，所有输出通过 CommandResult 表达。

**对外接口：** exit_command、plan_command、do_command、compact_command、resume_command、clear_command、help_command、status_command、memory_command、permission_command、session_command、review_command。

**依赖：** okcode.commands.models、okcode.models。

### okcode.commands.defaults

**职责：** 组装十二条内置命令定义。

**对外接口：** build_default_command_registry() -> CommandRegistry。

**默认命令表：**

| 命令 | 类型 | 行为 |
|------|------|------|
| exit | UI | 关闭 TUI 进程 |
| plan | UI | 切换运行时模式到 PLAN |
| do | PROMPT | 切回 DEFAULT，注入既有执行指令并触发回合 |
| compact | UI | 手动触发上下文压缩 |
| resume | UI | 打开历史会话列表，选中后从最后一个 compact 标记之后恢复 |
| clear | UI | 关闭当前会话存档，开启新会话，清空内存消息并归零累计值 |
| help | LOCAL | 按名字典序输出“名字 + 一句描述”两列列表 |
| status | LOCAL | 输出运行时模式、累计 Token、工具数、记忆条目数、模型名、工作目录 |
| memory | LOCAL | 输出项目层与用户层记忆文件名列表 |
| permission | LOCAL | 输出当前运行时模式字符串 |
| session | LOCAL | 输出当前 session 标识和存档文件路径 |
| review | PROMPT | 注入固定代码审查请求消息并触发回合 |

### okcode.commands.dispatcher

**职责：** 串联解析、命令查找、未知命令提示和处理函数调用。

**对外接口：**

- CommandDispatcher(registry, parser=None)
- dispatch(text, context) -> DispatchResult

DispatchResult 区分四种情况：空输入、非命令、已知命令、未知命令。未知命令返回本地 notice，不调用 Provider。

### okcode.commands.completion

**职责：** 适配 prompt-toolkit 的补全协议。

**对外接口：** SlashCommandCompleter(registry)。

只在当前输入以斜杠开头且光标位于命令名片段时提供补全。隐藏命令不返回候选。

### okcode.conversation.ConversationSession

**职责调整：** 从硬编码斜杠分支改为实现 CommandConversationPort。

**新增/调整接口：**

- runtime_mode 属性。
- set_runtime_mode(mode)。
- stream_user_message(text, mode=None, tool_scope=None)。
- stream_do_instruction()。
- stream_manual_compaction()。
- restore_session(session_id)，恢复范围从最后一个 compact 标记之后开始。
- reset_session()。
- status_snapshot()。
- memory_snapshot()。
- permission_string()。
- session_snapshot()。

ConversationSession 需要新增累计输入 Token、累计输出 Token 和回合数。每次成功完成普通回合后累加 TokenUsage；/clear 调用 reset_session() 后归零。

stream_do_instruction() 沿用本 spec 实施前 /do 外部行为：如果没有已保存计划，返回既有 no_saved_plan 提示；如果存在已保存计划，则注入“请执行当前会话最近一次计划：...”文本，使用既有执行模式提示，并立即触发回合。

### okcode.sessions.store

**职责调整：** 支持当前会话轮换和恢复边界。

**新增/调整接口：**

- SessionJournal.close() 或等价的只读关闭标记，用于让 /clear 后旧 journal 不再接收新消息。
- SessionStore.create_journal() 继续生成新 session 标识和新存档路径。
- SessionStore.restore_after_last_compact(session_id) 或在 restore() 返回结构中提供最后 compact 标记之后的消息片段。

如果当前存档文件采用按需追加写入、没有常驻文件句柄，close 可以是 ConversationSession 侧切换引用，不需要真实关闭 OS 文件句柄；但行为上必须保证后续消息写入新 journal。

### okcode.context.manager

**职责调整：** 为 /resume 提供“最后一个 compact 标记”识别依据。

优先复用已有摘要边界消息或 compact 后写入历史的结构。如果当前历史没有可识别 compact 标记，恢复行为退化为既有完整合法前缀恢复，并保持损坏记录跳过、工具调用边界截断和超预算压缩约束。

### okcode.app.OkCodeApp

**职责调整：** 成为用户输入分流入口。

**新增字段：** CommandDispatcher、CommandContext。

**主要流程：**

1. 调用 ui.prompt() 读取输入。
2. 空输入继续下一轮。
3. 调用 dispatcher.dispatch(text, context)。
4. 非命令结果调用 conversation.stream_user_message(text, mode=conversation.runtime_mode)。
5. 命令结果按 ui_action、events、stream、forward 依次执行。
6. EXIT 动作调用现有 goodbye 和资源清理路径。
7. SELECT_SESSION 动作调用 ui.select_session()，再调用 conversation.restore_session()。
8. RESET_SESSION 动作调用 conversation.reset_session()，再渲染 notice。

### okcode.terminal.TerminalUI

**职责调整：** 仍然只负责 UI，但新增命令友好能力。

**新增/调整接口：**

- set_command_registry(registry)。
- set_runtime_mode(mode)。
- clear_screen()。
- render_event(event) 支持新增命令事件。

构造 PromptSession 时注入 SlashCommandCompleter 和 bottom_toolbar 回调。状态栏回调读取当前 RuntimeMode，显示 [DEFAULT] 或 [PLAN]。测试替身 session 仍可注入，避免终端测试依赖真实交互。

### okcode.models

**新增事件模型：**

- CommandNotice(message, level)：展示命令提示、错误或普通状态。
- CommandHelp(entries)：展示两列帮助列表。
- CommandStatus(snapshot)：展示 /status 六项信息。
- CommandMemory(snapshot)：展示记忆文件名列表。
- CommandSession(snapshot)：展示当前会话标识。
- RuntimeModeChanged(mode, message)：展示模式切换结果。

这些事件加入 TurnEvent 联合类型，由 TerminalUI.render_event() 统一展示。

### 测试模块

新增或调整：

- tests/unit/test_commands_parser.py：空输入、非命令、大小写、参数保留、未知命令。
- tests/unit/test_commands_registry.py：名称冲突、别名冲突、隐藏命令、字典序帮助、补全候选。
- tests/unit/test_commands_handlers.py：十二条内置命令的 CommandResult。
- tests/unit/test_commands_dispatcher.py：分流器不误调用 Provider、未知命令帮助提示。
- tests/unit/test_terminal.py：补全器接入、状态栏模式标记、清屏事件、命令事件渲染。
- tests/unit/test_app.py：应用层命令分流、退出、resume、clear、forward 到 Agent。
- tests/unit/test_conversation.py：stream_user_message、stream_do_instruction、reset_session、累计 Token 和恢复边界。
- tests/unit/test_sessions.py：新 journal 轮换和最后 compact 标记之后恢复。

## 模块交互

### 普通输入

TerminalUI.prompt() -> OkCodeApp.run() -> CommandDispatcher.dispatch(普通文本) -> 非命令结果 -> ConversationSession.stream_user_message(普通文本, mode=current_runtime_mode) -> Provider.stream() -> TerminalUI.render_event(...)。

### /plan

用户输入 /plan -> plan_command 调用 conversation.set_runtime_mode(PLAN) -> RuntimeModeChanged(mode=PLAN) -> OkCodeApp 调用 ui.set_runtime_mode(PLAN) -> 下一次 prompt 底部状态栏显示 [PLAN]。

### /do

用户输入 /do -> do_command 调用 conversation.set_runtime_mode(DEFAULT) -> 返回 conversation.stream_do_instruction() -> 如果存在保存计划，则注入既有执行指令文本并立即触发 Provider；否则输出既有 no_saved_plan 提示。

### /clear

用户输入 /clear -> clear_command 返回 RESET_SESSION -> OkCodeApp 调用 conversation.reset_session() -> 旧 journal 停止写入，新 journal 创建，内存消息和累计值归零 -> TerminalUI 渲染 notice。

### /review

用户输入 /review -> review_command 构造固定文本 -> ForwardedUserMessage(content=固定代码审查请求, runtime_mode=DEFAULT, tool_scope=CURRENT_MODE, preset_name=review) -> ConversationSession.stream_user_message(...)。

固定文本为：

Please review the current git diff for code changes. Focus on:
1. Logic errors
2. Security issues
3. Performance problems
4. Code style

命令处理器不读取 git diff、不调用工具、不预先收集任何外部上下文。

### /resume

用户输入 /resume -> resume_command 返回 SELECT_SESSION -> OkCodeApp 调用 TerminalUI.select_session(...) -> 用户选择会话 -> ConversationSession.restore_session(session_id) -> 从最后一个 compact 标记之后恢复；若无 compact 标记则沿用合法前缀恢复。

## 文件组织

项目文件：

- src/okcode/app.py：接入 CommandDispatcher，统一分流用户输入。
- src/okcode/cli.py：构造默认命令注册中心并传给 App/UI。
- src/okcode/conversation.py：实现 CommandConversationPort，新增运行时模式、会话重置、累计 Token、/do 兼容入口。
- src/okcode/models.py：新增命令展示、状态、模式变更事件。
- src/okcode/terminal.py：接入补全器、状态栏、清屏和命令事件渲染。
- src/okcode/sessions/store.py：支持新会话 journal 轮换和最后 compact 标记恢复。
- src/okcode/commands/__init__.py：导出命令系统公共接口。
- src/okcode/commands/models.py：命令模型、结果、端口协议。
- src/okcode/commands/parser.py：斜杠解析。
- src/okcode/commands/registry.py：注册、冲突检测、别名、补全候选。
- src/okcode/commands/handlers.py：十二条内置命令处理函数。
- src/okcode/commands/defaults.py：组装十二条内置命令。
- src/okcode/commands/dispatcher.py：应用层分流器。
- src/okcode/commands/completion.py：prompt-toolkit 补全适配。

测试文件：

- tests/unit/test_commands_parser.py
- tests/unit/test_commands_registry.py
- tests/unit/test_commands_handlers.py
- tests/unit/test_commands_dispatcher.py
- tests/unit/test_app.py
- tests/unit/test_conversation.py
- tests/unit/test_sessions.py
- tests/unit/test_terminal.py

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 命令层位置 | 新增 okcode.commands 包 | 避免继续扩大 ConversationSession.stream_turn() 的分支，也让注册、帮助、补全共用一份元数据。 |
| 分流入口 | OkCodeApp.run() 先分流，再调用会话 | 用户回车入口最接近应用生命周期，方便处理退出、清屏、会话选择和 /clear 会话轮换。 |
| 运行时模式名称 | RuntimeMode.DEFAULT / PLAN | 避免与现有工具权限规则的 strict/default/allow 混淆，同时满足状态栏 [DEFAULT]/[PLAN]。 |
| /do 语义 | 保持旧外部行为 | 用户明确要求 /do 为提示词类，切回默认模式后注入现有执行指令并立即触发回合。 |
| /clear 语义 | 新建会话边界，不删除旧存档 | 满足关闭当前会话、开启新会话、清空内存消息和归零累计值，同时保留历史可恢复性。 |
| /review 语义 | 只注入固定文本，不预读上下文 | 用户明确要求不读 git diff、不收集外部上下文；命令处理器只负责注入固定消息。 |
| /help 输出 | 字典序两列列表 | 用户明确要求只输出“名字 + 一句描述”，避免展示过多元数据。 |
| /status 输出 | 固定六项 | 用户明确指定六项信息，避免扩展状态输出造成测试和 UI 不稳定。 |
| UI 解耦方式 | 命令返回事件和 UI 动作，不直接调用 Console | 命令逻辑可用无终端替身测试，也便于未来替换 TUI 框架。 |
| 补全实现 | 适配 prompt-toolkit Completer | 当前输入已经使用 PromptSession，最小改动即可支持 Tab 补全和多候选菜单。 |

## Spec 覆盖关系

| Spec | Plan 覆盖点 |
|------|-------------|
| F1 | CommandDefinition、CommandRegistry、启动期冲突检测 |
| F2 | CommandParser、CommandDispatcher |
| F3 | CommandKind、十二条命令处理函数 |
| F4 | RuntimeMode、TerminalUI 状态栏、ConversationSession runtime_mode |
| F5 | OkCodeApp.run() 分流流程 |
| F6 | SlashCommandCompleter、隐藏命令过滤 |
| F7 | CommandHelp 事件、注册中心字典序可见命令列表 |
| F8 | commands.defaults 的十二条内置命令 |
| F9 | review_command 的固定文本 |
| F10 | reset_session、session_snapshot、累计 Token 和回合计数 |

## 风险与约束

- “当前权限模式”在本阶段文档中解释为运行时模式 default/plan，不替代现有工具权限系统。实现时变量命名使用 RuntimeMode，避免与 PermissionManager.mode 混淆。
- /do 必须保留旧外部行为，因此不能简化为“只切回 DEFAULT”。它需要继续复用保存计划和既有执行指令注入。
- /clear 会断开当前会话存档并开启新会话，是比普通清屏更重的状态变更；测试必须验证旧存档未删除且新消息写入新存档。
- /review 的固定文本中提到 git diff，但命令处理器不得预先读取 diff。该约束验证的是命令层不做上下文采集。
- /resume 的“最后一个 compact 标记”依赖现有摘要边界是否可稳定识别；如果旧会话没有该标记，必须退化为既有合法前缀恢复，不应恢复失败。
