# OkCode 第九阶段：命令注册与分发机制 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。实现开始前需先审批本 checklist。

## 实现完整性

- [ ] 命令核心模型已实现并可导入（验证：运行 `uv run python -c "from okcode.commands import CommandKind, RuntimeMode; assert CommandKind.LOCAL and RuntimeMode.DEFAULT"`，期望无异常）。
- [ ] 斜杠解析器正确区分空输入、普通输入和命令输入（验证：运行 `uv run pytest tests/unit/test_commands_parser.py -q`，期望全部通过）。
- [ ] 命令注册中心在构造期发现名称、别名和别名撞名冲突（验证：运行 `uv run pytest tests/unit/test_commands_registry.py -q -k "conflict or duplicate"`，期望全部通过）。
- [ ] 默认注册中心只暴露十二条内置命令且按名字典序输出（验证：运行 `uv run pytest tests/unit/test_commands_registry.py -q -k "default or visible"`，期望包含 exit、plan、do、compact、resume、clear、help、status、memory、permission、session、review）。
- [ ] 命令事件模型可被终端渲染层消费（验证：运行 `uv run pytest tests/unit/test_models.py tests/unit/test_terminal.py -q -k "Command or RuntimeMode"`，期望全部通过）。
- [ ] 纯本地类命令不会创建 Provider 请求（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py -q -k "help or status or memory or permission or session"`，期望 FakeProvider 调用数为 0）。
- [ ] 影响界面类命令只返回 UI 动作或本地事件流（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py -q -k "exit or plan or compact or resume or clear"`，期望无普通 Agent forward）。
- [ ] 提示词类命令按 spec 注入消息并立即进入回合入口（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py -q -k "do or review"`，期望 /do 使用既有执行入口，/review 使用固定文本）。

## 命令行为

- [ ] /help 输出按命令名字典序排序的两列列表（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_terminal.py -q -k "help"`，期望每行只有命令名字和一句描述）。
- [ ] /status 输出六项指定信息（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_terminal.py -q -k "status"`，期望包含运行时模式、累计 Token 输入/输出、工具数量、已加载记忆条目数、模型名、当前工作目录）。
- [ ] /permission 输出与 /status 相同形式的运行时模式字符串（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py -q -k "permission or status"`，期望两处模式字符串一致）。
- [ ] /memory 只列项目层和用户层记忆文件名，不展开内容（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_commands_handlers.py -q -k "memory_snapshot or memory"`，期望输出不包含文件正文）。
- [ ] /session 至少输出当前 session 标识和会话存档文件路径（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_conversation.py -q -k "session_snapshot or session"`，期望字段完整）。
- [ ] /plan 切换到 PLAN 并更新状态栏（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_terminal.py -q -k "plan or runtime"`，期望状态栏显示 [PLAN]）。
- [ ] /do 先切回 DEFAULT，再复用旧执行计划外部行为（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_commands_handlers.py -q -k "do or no_saved_plan"`，期望有保存计划时触发 Provider，无保存计划时返回既有提示）。
- [ ] /compact 沿用手动压缩、摘要失败熔断和原子历史替换语义（验证：运行 `uv run pytest tests/unit/test_conversation.py -q -k "compact or summary or circuit"`，期望全部通过）。
- [ ] /resume 打开历史会话列表并从最后一个 compact 标记之后恢复（验证：运行 `uv run pytest tests/unit/test_sessions.py tests/unit/test_conversation.py -q -k "restore or compact"`，期望有标记时只恢复标记之后，无标记时退化为合法前缀恢复）。
- [ ] /clear 建立新会话边界且不删除旧存档（验证：运行 `uv run pytest tests/unit/test_sessions.py tests/unit/test_conversation.py tests/unit/test_app.py -q -k "clear or reset_session or journal"`，期望旧文件保留，新消息写入新 journal，内存消息、累计 Token 和回合数归零）。
- [ ] /review 不读取 git diff、不收集外部上下文，只注入固定审查请求文本（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py tests/integration/test_tool_turn.py -q -k "review"`，期望 Provider 收到固定四项文本，命令层无文件或 diff 读取调用）。
- [ ] /exit 沿用现有 TUI 清理与退出路径（验证：运行 `uv run pytest tests/unit/test_app.py -q -k "exit"`，期望 show_goodbye 被调用且返回码为 0）。

## 分流与补全

- [ ] 用户回车入口先走命令分流器（验证：运行 `uv run pytest tests/unit/test_app.py tests/unit/test_commands_dispatcher.py -q`，期望命令输入不进入普通文本路径，非命令输入进入 Agent）。
- [ ] 空输入早返回且不触发命令或 Agent（验证：运行 `uv run pytest tests/unit/test_commands_parser.py tests/unit/test_commands_dispatcher.py tests/unit/test_app.py -q -k "empty"`，期望 Provider 调用数为 0）。
- [ ] 未知命令输出 /help 引导且不调用 Provider（验证：运行 `uv run pytest tests/unit/test_commands_dispatcher.py -q -k "unknown"`，期望 CommandNotice 包含 /help）。
- [ ] 命令名大小写不敏感，参数正文保留语义（验证：运行 `uv run pytest tests/unit/test_commands_parser.py tests/unit/test_commands_dispatcher.py -q -k "case or args"`，期望 /STATUS now 命中 status 且 args 为 now）。
- [ ] Tab 补全只展示非隐藏命令候选（验证：运行 `uv run pytest tests/unit/test_commands_registry.py tests/unit/test_terminal.py -q -k "complete or completion"`，期望隐藏命令不出现）。
- [ ] 补全在唯一匹配、多匹配和无匹配场景下行为正确（验证：运行 `uv run pytest tests/unit/test_terminal.py -q -k "completion"`，期望唯一匹配可补全，多匹配展示候选，无匹配不补全）。

## 集成

- [ ] OkCodeApp 正确串联 CommandDispatcher、ConversationSession 和 TerminalUI（验证：运行 `uv run pytest tests/unit/test_app.py -q`，期望普通输入、/exit、/resume、/clear、/review 路径全部通过）。
- [ ] CLI 装配默认命令注册中心并传给 App/UI（验证：运行 `uv run pytest tests/unit/test_cli.py -q`，期望启动组装测试通过）。
- [ ] ConversationSession 不再硬编码 /compact、/permissions、/plan、/do 分支（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_app.py -q -k "command or stream_user_message or compact or do"`，期望命令行为在 App/Dispatcher 层验证）。
- [ ] 运行时模式不混淆现有工具权限系统 strict/default/allow（验证：运行 `uv run pytest tests/unit/test_permissions_manager.py tests/unit/test_permissions_rules.py tests/unit/test_conversation.py -q -k "permission or runtime"`，期望权限规则仍按原逻辑工作）。
- [ ] 新会话 journal 轮换不破坏会话恢复列表（验证：运行 `uv run pytest tests/unit/test_sessions.py tests/unit/test_app.py -q -k "journal or resumable or clear"`，期望旧 session 仍可列出）。
- [ ] 命令系统不改变工具调用、MCP 工具发现和权限确认流程（验证：运行 `uv run pytest tests/unit/test_tools_executor.py tests/unit/test_mcp_manager.py tests/integration/test_tool_turn.py -q`，期望全部通过）。

## 编译与测试

- [ ] 命令模块单测全部通过（验证：运行 `uv run pytest tests/unit/test_commands_parser.py tests/unit/test_commands_registry.py tests/unit/test_commands_handlers.py tests/unit/test_commands_dispatcher.py -q`）。
- [ ] 终端、应用、会话、会话存储相关单测全部通过（验证：运行 `uv run pytest tests/unit/test_terminal.py tests/unit/test_app.py tests/unit/test_conversation.py tests/unit/test_sessions.py -q`）。
- [ ] 完整测试集通过（验证：运行 `uv run pytest -q`，期望全部通过）。
- [ ] Ruff 格式检查通过（验证：运行 `uv run ruff format --check .`，期望无格式变更需求）。
- [ ] Ruff lint 检查通过（验证：运行 `uv run ruff check .`，期望无 lint 错误）。
- [ ] Git 空白检查通过（验证：运行 `git diff --check`，期望无输出且退出码为 0）。
- [ ] Git 状态只包含本阶段预期文件（验证：运行 `git status --short`，期望变更集中在 phase9 文档、commands 包、app/cli/conversation/models/terminal/sessions 和相关测试）。

## 端到端场景

- [ ] 场景 1：启动 OkCode 后输入 /help，看到按字典序排列的十二条命令两列列表，且没有模型请求（验证：用 App/FakeProvider 测试或手动 REPL 观察 Provider 调用数为 0）。
- [ ] 场景 2：输入 /plan 后状态栏显示 [PLAN]，下一条普通任务按计划模式使用只读工具（验证：运行 `uv run pytest tests/unit/test_app.py tests/unit/test_conversation.py -q -k "plan and runtime"`）。
- [ ] 场景 3：先生成保存计划，再输入 /do，系统切回 [DEFAULT] 并按旧执行计划指令立即触发回合（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_app.py -q -k "do"`）。
- [ ] 场景 4：输入 /clear 后继续发送普通消息，旧会话文件不再追加，新会话文件收到新消息，/session 显示新 session（验证：运行 `uv run pytest tests/unit/test_app.py tests/unit/test_sessions.py -q -k "clear or session"`）。
- [ ] 场景 5：输入 /review，Provider 收到固定代码审查请求文本，命令层没有读取 git diff 或文件内容（验证：运行 `uv run pytest tests/integration/test_tool_turn.py tests/unit/test_commands_handlers.py -q -k "review"`）。
- [ ] 场景 6：输入 /resume，从历史列表选择一个含 compact 标记的会话，恢复结果只包含最后 compact 标记之后的合法消息片段（验证：运行 `uv run pytest tests/unit/test_sessions.py tests/unit/test_conversation.py -q -k "resume or restore"`）。

