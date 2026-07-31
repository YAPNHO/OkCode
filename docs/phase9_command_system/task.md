# OkCode 第九阶段：命令注册与分发机制 Tasks

> 本任务拆解以已批准的 spec.md 和 plan.md 为实现基线。开发开始前必须继续完成并审批 checklist.md；审批前禁止编写实现代码。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | src/okcode/commands/__init__.py | 导出命令系统公共接口 |
| 新建 | src/okcode/commands/models.py | 命令枚举、数据结构、端口协议、结果模型 |
| 新建 | src/okcode/commands/parser.py | 斜杠输入解析 |
| 新建 | src/okcode/commands/registry.py | 命令注册、冲突检测、别名解析、帮助排序、补全候选 |
| 新建 | src/okcode/commands/handlers.py | 十二条内置命令处理函数 |
| 新建 | src/okcode/commands/defaults.py | 组装默认命令注册中心 |
| 新建 | src/okcode/commands/dispatcher.py | 用户输入分流器 |
| 新建 | src/okcode/commands/completion.py | prompt-toolkit 斜杠命令补全适配 |
| 修改 | src/okcode/models.py | 新增命令事件、运行时模式事件并扩展 TurnEvent |
| 修改 | src/okcode/conversation.py | 实现命令会话端口、运行时模式、/do 兼容、/clear 会话重置、累计 Token |
| 修改 | src/okcode/sessions/store.py | 支持会话 journal 轮换和最后 compact 标记后的恢复输入 |
| 修改 | src/okcode/app.py | 接入 CommandDispatcher，统一处理命令与普通输入 |
| 修改 | src/okcode/cli.py | 构造命令注册中心并注入 App/UI |
| 修改 | src/okcode/terminal.py | 接入补全、状态栏、清屏和命令事件渲染 |
| 新建 | tests/unit/test_commands_parser.py | 解析器单元测试 |
| 新建 | tests/unit/test_commands_registry.py | 注册中心、别名、冲突和补全候选测试 |
| 新建 | tests/unit/test_commands_handlers.py | 十二条内置命令处理函数测试 |
| 新建 | tests/unit/test_commands_dispatcher.py | 分流器测试 |
| 修改 | tests/unit/test_app.py | 应用层命令分流、退出、resume、clear、forward 测试 |
| 修改 | tests/unit/test_conversation.py | 会话端口、运行时模式、/do、累计 Token、reset_session 测试 |
| 修改 | tests/unit/test_sessions.py | journal 轮换和 compact 后恢复测试 |
| 修改 | tests/unit/test_terminal.py | 补全、状态栏、清屏、命令事件渲染测试 |
| 修改 | tests/integration/test_tool_turn.py | 普通输入、/do、/review 端到端回归 |

## T1：定义命令核心模型

**文件：** src/okcode/commands/models.py、src/okcode/commands/__init__.py
**依赖：** 无
**步骤：**
1. 定义 CommandKind，包含 LOCAL、UI、PROMPT。
2. 定义 RuntimeMode，包含 DEFAULT、PLAN，并提供用于状态栏和 /permission 输出的字符串值。
3. 定义 ParsedCommand、CommandDefinition、ForwardedUserMessage、CommandResult、CompletionCandidate。
4. 定义 CommandUiAction，包含 NONE、CLEAR_SCREEN、EXIT、SELECT_SESSION、RESET_SESSION。
5. 定义 CommandStatusSnapshot、CommandMemorySnapshot、CommandSessionSnapshot。
6. 定义 CommandConversationPort Protocol，包含 plan.md 中列出的会话能力。
7. 在 __init__.py 导出公共类型。

**验证：** 运行 uv run python -c "from okcode.commands import CommandKind, RuntimeMode; assert CommandKind.LOCAL and RuntimeMode.DEFAULT"，期望导入成功。

## T2：实现斜杠解析器

**文件：** src/okcode/commands/parser.py、tests/unit/test_commands_parser.py
**依赖：** T1
**步骤：**
1. 定义 ParseResult，区分 empty、is_command、command、text。
2. 实现 CommandParser.parse(text)。
3. 空白输入返回 empty=True，不产生 ParsedCommand。
4. 非斜杠输入返回 is_command=False，并保留原始用户文本。
5. 斜杠输入按第一个空格拆出命令名和参数，命令名去斜杠并小写归一。
6. 添加大小写、参数保留、未知命令不在解析层处理等测试。

**验证：** 运行 uv run pytest tests/unit/test_commands_parser.py -q，期望全部通过。

## T3：实现命令注册中心

**文件：** src/okcode/commands/registry.py、tests/unit/test_commands_registry.py
**依赖：** T1
**步骤：**
1. 实现 CommandRegistry 构造函数，接收 CommandDefinition 列表。
2. 建立 name/alias 到 CommandDefinition 的归一化索引。
3. 检查命令名重复、别名重复、别名撞命令名，并在构造期抛出 ValueError。
4. 实现 resolve(name)，支持命令名和别名大小写不敏感解析。
5. 实现 visible_commands()，按命令名字典序返回非隐藏命令。
6. 实现 completion_candidates(prefix)，只返回非隐藏命令和可见别名候选。
7. 添加冲突、隐藏命令、字典序和补全候选测试。

**验证：** 运行 uv run pytest tests/unit/test_commands_registry.py -q，期望全部通过。

## T4：新增命令事件模型

**文件：** src/okcode/models.py、tests/unit/test_models.py
**依赖：** T1
**步骤：**
1. 新增 CommandNoticeLevel 和 CommandNotice。
2. 新增 CommandHelpEntry、CommandHelp，用于两列帮助列表。
3. 新增 CommandStatus、CommandMemory、CommandSession。
4. 新增 RuntimeModeChanged，用于 /plan 与 /do 模式变化提示。
5. 将新增事件加入 TurnEvent 联合类型。
6. 添加模型构造和默认值测试，确保不破坏已有 TokenUsage、PermissionStatus 等模型。

**验证：** 运行 uv run pytest tests/unit/test_models.py -q，期望全部通过。

## T5：实现纯本地命令处理函数

**文件：** src/okcode/commands/handlers.py、tests/unit/test_commands_handlers.py
**依赖：** T1、T3、T4
**步骤：**
1. 实现 help_command：从 registry.visible_commands() 生成 CommandHelp，输出命令名和一句描述。
2. 实现 status_command：读取 conversation.status_snapshot() 并返回 CommandStatus。
3. 实现 memory_command：读取 conversation.memory_snapshot() 并返回 CommandMemory。
4. 实现 permission_command：读取 conversation.permission_string()，返回只包含运行时模式字符串的 CommandNotice。
5. 实现 session_command：读取 conversation.session_snapshot() 并返回 CommandSession。
6. 用 FakeCommandConversationPort 覆盖每个命令，断言不产生 forward、不产生 stream、不请求 Provider。

**验证：** 运行 uv run pytest tests/unit/test_commands_handlers.py -q -k "help or status or memory or permission or session"，期望全部通过。

## T6：实现影响界面类命令处理函数

**文件：** src/okcode/commands/handlers.py、tests/unit/test_commands_handlers.py
**依赖：** T1、T4、T5
**步骤：**
1. 实现 exit_command，返回 CommandUiAction.EXIT。
2. 实现 plan_command，调用 conversation.set_runtime_mode(RuntimeMode.PLAN)，返回 RuntimeModeChanged。
3. 实现 compact_command，返回 conversation.stream_manual_compaction() 事件流。
4. 实现 resume_command，返回 CommandUiAction.SELECT_SESSION。
5. 实现 clear_command，返回 CommandUiAction.RESET_SESSION；具体 reset 由 App 调用 conversation.reset_session()。
6. 添加测试，断言 /plan 不产生 Provider forward，/clear 不直接删除历史，/compact 只返回事件流。

**验证：** 运行 uv run pytest tests/unit/test_commands_handlers.py -q -k "exit or plan or compact or resume or clear"，期望全部通过。

## T7：实现提示词类命令处理函数

**文件：** src/okcode/commands/handlers.py、tests/unit/test_commands_handlers.py
**依赖：** T1、T4
**步骤：**
1. 实现 do_command：先调用 conversation.set_runtime_mode(RuntimeMode.DEFAULT)，再返回 conversation.stream_do_instruction()。
2. 实现 review_command：构造固定代码审查请求文本，不读取 git diff，不调用任何工具，不读取工作区文件。
3. review_command 返回 ForwardedUserMessage，content 必须等于 spec.md 中的固定四项文本。
4. 添加测试，断言 /review 的 content 精确匹配固定文本，且 FakePort 没有任何 diff 或文件读取调用。
5. 添加测试，断言 /do 会先切 DEFAULT，再使用 stream_do_instruction()。

**验证：** 运行 uv run pytest tests/unit/test_commands_handlers.py -q -k "do or review"，期望全部通过。

## T8：组装默认命令注册中心

**文件：** src/okcode/commands/defaults.py、tests/unit/test_commands_registry.py
**依赖：** T3、T5、T6、T7
**步骤：**
1. 实现 build_default_command_registry()。
2. 注册十二条内置命令：exit、plan、do、compact、resume、clear、help、status、memory、permission、session、review。
3. 为每条命令填写一句描述、usage、kind、argument_hint、hidden=False。
4. 确认不注册旧的 /permissions 别名，避免与用户最新十二条命令范围不一致。
5. 添加测试，断言默认注册中心刚好包含十二条可见命令，且 visible_commands() 字典序排序。

**验证：** 运行 uv run pytest tests/unit/test_commands_registry.py -q -k "default or visible"，期望全部通过。

## T9：实现命令分流器

**文件：** src/okcode/commands/dispatcher.py、tests/unit/test_commands_dispatcher.py
**依赖：** T2、T3、T4、T8
**步骤：**
1. 定义 DispatchResult，表达 empty、not_command、command_result 三种稳定输出。
2. CommandDispatcher.dispatch() 先调用 CommandParser。
3. 空输入返回已处理结果。
4. 非命令返回普通文本。
5. 命令未命中时返回 CommandNotice，提示未知命令并引导 /help，不调用 Provider。
6. 命中命令时调用对应 handler，并传入 CommandContext。
7. 添加测试覆盖空输入、普通输入、未知命令、大小写命令和别名解析。

**验证：** 运行 uv run pytest tests/unit/test_commands_dispatcher.py -q，期望全部通过。

## T10：实现斜杠命令补全器

**文件：** src/okcode/commands/completion.py、tests/unit/test_commands_registry.py、tests/unit/test_terminal.py
**依赖：** T3、T8
**步骤：**
1. 实现 SlashCommandCompleter，适配 prompt-toolkit Completer。
2. 仅当输入以斜杠开头且光标在命令名片段时返回候选。
3. 候选从 registry.completion_candidates(prefix) 获取。
4. 补全文本包含斜杠命令名，显示内容包含一句描述。
5. 添加测试覆盖唯一候选、多候选、无候选、隐藏命令不出现。

**验证：** 运行 uv run pytest tests/unit/test_terminal.py tests/unit/test_commands_registry.py -q -k "complete or completion"，期望全部通过。

## T11：扩展会话状态与快照端口

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py
**依赖：** T1、T4
**步骤：**
1. 为 ConversationSession 增加 runtime_mode 字段，默认 RuntimeMode.DEFAULT。
2. 实现 set_runtime_mode() 和 permission_string()。
3. 新增累计输入 Token、累计输出 Token、回合数状态。
4. 在成功完成普通回合后累加 TokenUsage 的 input_tokens 和 output_tokens；缺失值按 0 处理。
5. 实现 status_snapshot()，返回 spec 要求的六项信息。
6. 实现 session_snapshot()，返回当前 journal 的 session_id 和 path。
7. 添加测试覆盖默认模式、模式切换、Token 累加和快照字段。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q -k "runtime or status_snapshot or token or session_snapshot"，期望全部通过。

## T12：实现记忆文件快照

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py、tests/unit/test_memory_store.py
**依赖：** T11
**步骤：**
1. 在 ConversationSession 中保留 MemoryStore 或 MemoryPaths 可读引用，供命令端口读取文件名。
2. 实现 memory_snapshot()，分别枚举项目层和用户层记忆目录下的 Markdown 文件名。
3. 只返回文件名，不返回完整路径，不读取正文内容。
4. loaded_memory_item_count 使用项目层与用户层文件名数量合计。
5. 添加测试，创建项目层/用户层笔记文件，断言只返回文件名并按稳定顺序输出。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py tests/unit/test_memory_store.py -q -k "memory_snapshot or read_context"，期望全部通过。

## T13：重构普通消息入口

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py
**依赖：** T11
**步骤：**
1. 新增 stream_user_message(text, mode=None, tool_scope=None)。
2. 默认使用 self.runtime_mode；PLAN 使用只读工具和 TurnKind.PLAN；DEFAULT 使用全量工具和 TurnKind.NORMAL。
3. 将现有普通 Agent Loop 逻辑从 stream_turn() 迁入 stream_user_message()。
4. stream_turn() 仅保留兼容包装，不再硬编码 /compact、/permissions、/plan、/do 分支。
5. 更新测试，断言 PLAN 普通输入使用只读工具，DEFAULT 普通输入使用全量工具。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q -k "plan or normal or stream_user_message"，期望全部通过。

## T14：实现 /do 旧行为兼容入口

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py
**依赖：** T13
**步骤：**
1. 实现 stream_do_instruction()。
2. 无 saved_plan 时返回既有 AgentStopped(NO_SAVED_PLAN, “没有可执行的计划，请先使用 /plan 生成计划。”)。
3. 有 saved_plan 时注入既有文本 “请执行当前会话最近一次计划：\n” + saved_plan.content。
4. 使用既有执行计划 TurnKind.DO 和全量工具，保持旧外部行为。
5. 添加测试覆盖有计划、无计划、切回 DEFAULT 后触发。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q -k "do or no_saved_plan"，期望全部通过。

## T15：实现手动压缩端口

**文件：** src/okcode/conversation.py、tests/unit/test_conversation.py
**依赖：** T13
**步骤：**
1. 将现有 _handle_compact_command() 改造成 stream_manual_compaction() 公共端口。
2. 保留低预算强制摘要、无历史可压缩提示、摘要失败熔断和原子提交语义。
3. 去除 stream_turn() 中直接识别 /compact 的分支。
4. 更新压缩相关测试从 stream_manual_compaction() 入口验证。

**验证：** 运行 uv run pytest tests/unit/test_conversation.py -q -k "compact or summary or circuit"，期望全部通过。

## T16：实现 session journal 轮换和 /clear 会话重置

**文件：** src/okcode/sessions/store.py、src/okcode/conversation.py、tests/unit/test_sessions.py、tests/unit/test_conversation.py
**依赖：** T11
**步骤：**
1. 明确 SessionJournal 的关闭语义：如果没有常驻文件句柄，close 可作为停止继续使用旧 journal 的标记或无操作方法。
2. 在 ConversationSession.reset_session() 中保存旧 journal 引用，创建新的 SessionJournal。
3. 清空 self._messages，并通知 ContextManager 同步清空或恢复空历史状态。
4. 清空 saved_plan、累计输入 Token、累计输出 Token 和回合数。
5. 返回 CommandNotice 所需的重置结果信息。
6. 添加测试，断言 /clear 后旧存档文件仍存在但新消息写入新 journal，内存消息和累计值归零。

**验证：** 运行 uv run pytest tests/unit/test_sessions.py tests/unit/test_conversation.py -q -k "reset_session or journal or clear"，期望全部通过。

## T17：实现最后 compact 标记之后恢复

**文件：** src/okcode/sessions/store.py、src/okcode/conversation.py、tests/unit/test_sessions.py、tests/unit/test_conversation.py
**依赖：** T15、T16
**步骤：**
1. 定义可识别的 compact 标记来源，优先复用摘要成功后的边界消息或上下文摘要状态。
2. 在 SessionStore 或 ConversationSession 恢复流程中定位最后一个 compact 标记。
3. restore_session(session_id) 只恢复该标记之后的合法消息片段；没有标记时退化为既有合法前缀恢复。
4. 保留损坏记录跳过、工具调用边界截断和恢复后超预算压缩逻辑。
5. 添加测试覆盖有 compact 标记、无 compact 标记、损坏行、未完成工具调用四种情况。

**验证：** 运行 uv run pytest tests/unit/test_sessions.py tests/unit/test_conversation.py -q -k "restore or compact"，期望全部通过。

## T18：接入应用层分流

**文件：** src/okcode/app.py、tests/unit/test_app.py
**依赖：** T9、T11、T13、T14、T15、T16、T17
**步骤：**
1. OkCodeApp 构造函数新增 CommandDispatcher 或 CommandRegistry 参数。
2. run() 读取输入后调用 dispatcher.dispatch()。
3. empty 结果直接继续下一轮；not_command 调用 conversation.stream_user_message()。
4. 命令结果按 events、stream、ui_action、forward 顺序处理。
5. EXIT 调用现有 show_goodbye 并返回 0。
6. SELECT_SESSION 调用 ui.select_session()，再调用 conversation.restore_session()。
7. RESET_SESSION 调用 conversation.reset_session() 并渲染 notice。
8. forward 调用 conversation.stream_user_message(forward.content, mode=forward.runtime_mode, tool_scope=forward.tool_scope)。
9. 添加测试覆盖 /exit、/resume、/clear、/review、普通文本。

**验证：** 运行 uv run pytest tests/unit/test_app.py -q，期望全部通过。

## T19：接入 CLI 装配

**文件：** src/okcode/cli.py、tests/unit/test_cli.py
**依赖：** T8、T18
**步骤：**
1. 在 main() 中构造 build_default_command_registry()。
2. 将 registry 或 dispatcher 注入 OkCodeApp。
3. 将命令注册中心传给 TerminalUI，供补全使用。
4. 保留 ConfigError、MCP warning、provider/memory/mcp cleanup 的既有生命周期。
5. 添加 CLI 装配测试，断言启动组装能创建命令注册中心，且注册冲突会走启动失败路径。

**验证：** 运行 uv run pytest tests/unit/test_cli.py -q，期望全部通过。

## T20：接入终端补全、状态栏和命令事件渲染

**文件：** src/okcode/terminal.py、tests/unit/test_terminal.py
**依赖：** T4、T10、T18
**步骤：**
1. TerminalUI 新增 set_command_registry()，构造 PromptSession 时注入 SlashCommandCompleter。
2. TerminalUI 新增 set_runtime_mode()，底部状态栏显示 [DEFAULT] 或 [PLAN]。
3. 实现 clear_screen()，适配 Windows 终端并保持测试替身可观测。
4. render_event() 支持 CommandNotice、CommandHelp、CommandStatus、CommandMemory、CommandSession、RuntimeModeChanged。
5. /help 渲染为两列对齐列表，只包含命令名字和一句描述。
6. 添加测试覆盖状态栏、补全器、清屏和各命令事件可见文本。

**验证：** 运行 uv run pytest tests/unit/test_terminal.py -q，期望全部通过。

## T21：更新旧斜杠命令测试与集成回归

**文件：** tests/unit/test_app.py、tests/unit/test_conversation.py、tests/integration/test_tool_turn.py
**依赖：** T18、T20
**步骤：**
1. 将旧的 ConversationSession.stream_turn('/plan ...')、stream_turn('/do')、stream_turn('/compact') 断言迁移到 App/Dispatcher 层。
2. 保留 /do 外部行为兼容测试：有计划触发执行，无计划返回既有提示。
3. 添加 /review 集成测试，断言 Provider 收到固定代码审查请求文本，命令层没有预读 diff。
4. 保留普通文本、工具调用、权限确认、MCP 工具、上下文压缩的既有回归路径。

**验证：** 运行 uv run pytest tests/unit/test_app.py tests/unit/test_conversation.py tests/integration/test_tool_turn.py -q，期望全部通过。

## T22：全量验证与静态检查

**文件：** 全项目
**依赖：** T1-T21
**步骤：**
1. 运行完整单元与集成测试。
2. 运行 Ruff 格式检查和 lint 检查。
3. 运行 git diff --check，确认没有空白错误。
4. 查看 git status --short，确认只包含本阶段预期文件。
5. 如任一验证失败，回到对应任务修复并重跑验证。

**验证：** 依次运行 uv run pytest -q、uv run ruff format --check .、uv run ruff check .、git diff --check，期望全部通过。

## 执行顺序

T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8 -> T9 -> T10
T11 -> T12 -> T13 -> T14 -> T15 -> T16 -> T17
T18 -> T19 -> T20 -> T21 -> T22

## 自检结果

- plan 覆盖：commands 包、ConversationSession、SessionStore、OkCodeApp、TerminalUI、CLI 和测试模块均有对应任务。
- 依赖链：先模型/解析/注册，再处理函数/分流器，再会话端口，最后 App/UI/CLI 接入，无循环依赖。
- 验证完整性：每个任务都包含具体 pytest 或项目检查命令。
- 范围控制：不实现用户自定义命令、动态提示词、命令级权限控制，也不在 /review 里读取 git diff。
