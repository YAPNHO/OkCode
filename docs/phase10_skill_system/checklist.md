# OkCode 第十阶段：Skill 系统 Checklist

> 每一项都必须通过运行代码、检查 Provider 请求、观察终端输出或对比测试结果来验证。验收聚焦用户可见行为和系统边界，不依赖逐行阅读实现。

## 实现完整性

- [ ] Skill 核心包已实现并可导入（验证：运行 `uv run pytest tests/unit/test_skills_frontmatter.py tests/unit/test_skills_discovery.py tests/unit/test_skills_catalog.py tests/unit/test_skills_activation.py -q`，期望全部通过）
- [ ] 单文件 Skill 能解析合法 YAML frontmatter 和 Markdown SOP 正文（验证：运行 `uv run pytest tests/unit/test_skills_frontmatter.py -q -k "frontmatter"`，期望合法样例被解析为 name、description、tools、mode、history、model 和 body）
- [ ] frontmatter 缺失、YAML 错误、必填字段缺失、字段类型错误、正文为空时只拒绝该 Skill（验证：运行 `uv run pytest tests/unit/test_skills_frontmatter.py tests/unit/test_skills_discovery.py -q -k "invalid or error or empty"`，期望错误包含路径和原因）
- [ ] SOP 占位符 `{{name}}` 能被参数替换，缺参时报错，额外参数追加到 SOP 参数区块（验证：运行 `uv run pytest tests/unit/test_skills_frontmatter.py -q -k "placeholder or render"`，期望输出不包含未替换占位符）
- [ ] 目录型 Skill 能通过 `SKILL.md` 和可选 `tools/tools.yaml` 被发现（验证：运行 `uv run pytest tests/unit/test_skills_discovery.py -q -k "package or directory"`，期望目录包出现在有效 Skill 列表中）
- [ ] 目录型 Skill 专属工具能按 `skill__{skill_name}__{local_name}` 暴露并执行脚本协议（验证：运行 `uv run pytest tests/unit/test_skills_load_tool.py -q -k "script_tool"`，期望 stdin JSON、stdout JSON、失败路径都被覆盖）
- [ ] 内置 `commit`、`review`、`test` 三个样板按普通 Skill 机制发现和加载（验证：运行 `uv run pytest tests/unit/test_skills_catalog.py -q -k "builtin"`，期望三者来自内置来源且不注册为特殊斜杠命令）

## 发现与覆盖

- [ ] Skill 来源目录为内置、用户、项目三级，并按项目高于用户高于内置覆盖（验证：运行 `uv run pytest tests/unit/test_skills_discovery.py -q -k "roots or override"`，期望同名 Skill 最终使用项目版本）
- [ ] 被覆盖的低优先级 Skill 不出现在可用 Skill 列表和 LoadSkill 结果中（验证：运行 `uv run pytest tests/unit/test_skills_catalog.py -q -k "override"`，期望只返回最高优先级版本）
- [ ] 同一来源同名 Skill 启动级失败并指出冲突路径（验证：运行 `uv run pytest tests/unit/test_skills_discovery.py -q -k "conflict"`，期望抛出可诊断错误）
- [ ] 单个非法 Skill 不阻断其他合法 Skill（验证：运行 `uv run pytest tests/unit/test_skills_discovery.py tests/unit/test_skills_catalog.py -q -k "issue or invalid"`，期望合法 Skill 仍可 list 和 load）
- [ ] 热更新刷新后能反映新增、修改、删除和解析失败（验证：运行 `uv run pytest tests/unit/test_skills_catalog.py tests/unit/test_commands_handlers.py -q -k "refresh or hot or reload"`，期望 `/skill` 输出和 LoadSkill 目标变化符合文件状态）

## 启动与工具白名单

- [ ] 启动期只注入可用 Skill 的 name 和 description，不注入完整 SOP 或脚本内容（验证：运行 `uv run pytest tests/unit/test_prompt_builder.py tests/unit/test_prompt_runtime.py -q -k "available_skills"`，期望动态提示含可用 Skill 元数据但不含正文 SOP）
- [ ] 白名单引用不存在工具时启动失败并指出 Skill 名称和缺失工具名（验证：运行 `uv run pytest tests/unit/test_skills_catalog.py tests/unit/test_cli.py -q -k "missing_tool or whitelist"`，期望失败发生在启动装配或 catalog 校验阶段）
- [ ] `load_skill` 是系统级工具，始终可见且不受 Skill 白名单限制（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_skills_activation.py -q -k "load_skill"`，期望任意工具范围都包含 `load_skill`）
- [ ] 激活 Skill 后模型可见工具被收窄为白名单并集、专属工具和 `load_skill`（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_skills_activation.py -q -k "visible_tools or whitelist"`，期望白名单外工具不在 ProviderRequest.tools 中）
- [ ] ToolExecutor、权限确认、黑名单和工作区限制仍控制真实工具执行（验证：运行 `uv run pytest tests/unit/test_tools_executor.py tests/unit/test_permissions_manager.py tests/unit/test_permissions_blacklist.py -q`，期望全部通过）

## LoadSkill 与激活快照

- [ ] 模型能通过 `load_skill` 按名称激活完整 SOP（验证：运行 `uv run pytest tests/unit/test_skills_load_tool.py -q -k "shared"`，期望 ToolOutput 返回激活名称、版本和模式）
- [ ] 同一 Skill 重复 LoadSkill 会用当前最新有效定义替换旧快照，不重复追加提示词（验证：运行 `uv run pytest tests/unit/test_skills_load_tool.py tests/unit/test_skills_activation.py -q -k "reload or replace"`，期望 active 列表仍只有一个同名 Skill）
- [ ] 已激活 Skill 的完整 SOP 每轮请求都出现在“已激活的 Skill”动态区块（验证：运行 `uv run pytest tests/unit/test_prompt_builder.py tests/unit/test_conversation.py -q -k "active_skills"`，期望连续两轮 ProviderRequest 都包含同一快照）
- [ ] 多个 Skill 可同时激活且提示词边界清楚、顺序稳定（验证：运行 `uv run pytest tests/unit/test_skills_activation.py tests/unit/test_prompt_builder.py -q -k "multiple"`，期望两个 Skill 均出现且标题分隔稳定）
- [ ] 激活快照不受源文件热更新影响，直到 `/clear` 或显式重新加载同名 Skill（验证：运行 `uv run pytest tests/unit/test_skills_load_tool.py -q -k "snapshot or hot"`，期望源文件修改后旧快照仍保留，再次 LoadSkill 后切换新版本）
- [ ] 重新加载时目标 Skill 已删除或解析失败，旧快照保留并返回可诊断错误（验证：运行 `uv run pytest tests/unit/test_skills_load_tool.py -q -k "deleted or parse_failed"`，期望旧 active section 未变化）

## 执行模式

- [ ] shared 模式 Skill 在主 Agent Loop 中继续执行并按普通回合提交历史（验证：运行 `uv run pytest tests/integration/test_tool_turn.py -q -k "skill and shared"`，期望用户、助手工具调用、工具结果、最终助手消息完整提交）
- [ ] shared 模式取消、异常或失败时不留下半提交消息（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/integration/test_tool_turn.py -q -k "atomic or failed"`，期望历史不包含不完整工具配对）
- [ ] isolated 模式按 `none`、`recent`、`summary`、`all_safe` 选择历史（验证：运行 `uv run pytest tests/unit/test_skills_runner.py -q -k "history"`，期望四种模式输入历史符合配置）
- [ ] isolated 模式不把临时 messages 写入主历史，只把摘要或失败原因作为 LoadSkill 工具结果回流（验证：运行 `uv run pytest tests/unit/test_skills_runner.py tests/unit/test_skills_load_tool.py -q -k "isolated"`，期望主历史只看到工具结果摘要）
- [ ] 独立模式历史选择不会拆开 assistant tool_calls 与 tool result 配对（验证：运行 `uv run pytest tests/unit/test_skills_runner.py -q -k "tool_pairing"`，期望所有传入临时对话的历史均合法）
- [ ] Skill 指定模型时 ProviderRequest 使用 model_override；多个激活 Skill 指定不同模型时拒绝新激活（验证：运行 `uv run pytest tests/unit/test_models.py tests/integration/test_openai_sse.py tests/integration/test_anthropic_sse.py tests/unit/test_skills_activation.py -q -k "model"`，期望覆盖和冲突行为正确）

## `/skill` 命令与终端

- [ ] `/skill` 是内置本地命令，并出现在 `/help` 和补全候选中（验证：运行 `uv run pytest tests/unit/test_commands_registry.py tests/unit/test_commands_dispatcher.py -q -k "skill"`，期望 registry 能 resolve `skill`）
- [ ] 普通 Skill 不注册为斜杠命令，也不能通过同名斜杠命令直接触发（验证：运行 `uv run pytest tests/unit/test_commands_registry.py tests/unit/test_commands_dispatcher.py -q -k "skill"`，期望 Skill 名称不出现在 CommandRegistry）
- [ ] `/skill` 输出可加载 Skill 的名称、来源、说明和激活状态（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_terminal.py -q -k "skill"`，期望终端可见文本包含 Skill 名称和 active 标记）
- [ ] `/skill` 输出解析 issue 且不隐藏可诊断错误（验证：运行 `uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_terminal.py -q -k "issue"`，期望 warning/error 文本可见）
- [ ] `/clear` 清空主会话时同步清空已激活 Skill（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_commands_handlers.py -q -k "clear or reset"`，期望 active section 为空而可用 Skill 列表仍存在）

## 集成

- [ ] CLI 启动成功时注册默认工具、MCP 工具、SkillCatalog、LoadSkillTool、`/skill` 命令和提示词渲染器（验证：运行 `uv run pytest tests/unit/test_cli.py -q -k "skill or load_skill"`，期望装配对象齐全）
- [ ] 默认对话未使用 Skill 时仍保持原 DEFAULT/PLAN 工具范围和提示词行为（验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_prompt_modes.py tests/unit/test_prompt_runtime.py -q`，期望既有模式测试通过）
- [ ] LoadSkill 工具调用端到端后，下一次 Provider 请求看到已激活 Skill 完整 SOP（验证：运行 `uv run pytest tests/integration/test_tool_turn.py -q -k "skill"`，期望测试替身捕获到 active Skill section）
- [ ] 上下文压缩、会话恢复和长期记忆不被 Skill 激活破坏（验证：运行 `uv run pytest tests/unit/test_context_manager.py tests/unit/test_context_summary.py tests/unit/test_sessions.py tests/unit/test_memory_store.py -q`，期望全部通过）
- [ ] MCP 工具发现后可被 Skill 白名单引用，缺失 MCP 工具仍按启动期白名单规则失败（验证：运行 `uv run pytest tests/unit/test_mcp_manager.py tests/unit/test_mcp_config.py tests/unit/test_cli.py -q -k "mcp or skill"`，期望注册和失败路径正确）

## 编译与测试

- [ ] Skill 相关单元测试全部通过（验证：运行 `uv run pytest tests/unit/test_skills_frontmatter.py tests/unit/test_skills_discovery.py tests/unit/test_skills_catalog.py tests/unit/test_skills_activation.py tests/unit/test_skills_load_tool.py tests/unit/test_skills_runner.py -q`，期望全部通过）
- [ ] 命令、提示词、会话、终端、CLI 相关单元测试全部通过（验证：运行 `uv run pytest tests/unit/test_commands_registry.py tests/unit/test_commands_parser.py tests/unit/test_commands_handlers.py tests/unit/test_commands_dispatcher.py tests/unit/test_prompt_builder.py tests/unit/test_prompt_runtime.py tests/unit/test_conversation.py tests/unit/test_terminal.py tests/unit/test_app.py tests/unit/test_cli.py -q`，期望全部通过）
- [ ] Provider 和集成测试通过（验证：运行 `uv run pytest tests/integration -q`，期望全部通过）
- [ ] 全量测试通过（验证：运行 `uv run pytest -q`，期望全部通过）
- [ ] Ruff 格式检查通过（验证：运行 `uv run ruff format --check .`，期望退出码为 0）
- [ ] Ruff lint 通过（验证：运行 `uv run ruff check .`，期望退出码为 0）
- [ ] Git 空白检查通过（验证：运行 `git diff --check`，期望无输出且退出码为 0）

## 端到端场景

- [ ] 场景 1：用户启动 OkCode 后直接问“有哪些 Skill 可用？”或执行 `/skill`，终端列出 `commit`、`review`、`test` 及其说明，不显示完整 SOP（验证：运行对应单元/集成替身，观察 `/skill` 可见文本）
- [ ] 场景 2：用户说“用 review skill 看当前改动”，模型先调用 `load_skill`，随后下一轮请求包含 review 完整 SOP，并继续用白名单工具完成审查（验证：运行 `uv run pytest tests/integration/test_tool_turn.py -q -k "review_skill"`，期望 Provider 请求序列符合两阶段加载）
- [ ] 场景 3：用户修改已激活 review Skill 文件后继续对话，当前会话仍使用旧快照；再次 LoadSkill 后切换新 SOP（验证：运行热更新测试，观察两次 active section 内容不同且切换时机正确）
- [ ] 场景 4：用户执行 `/clear` 后继续对话，旧激活 Skill 不再出现在“已激活的 Skill”区块，但 `/skill` 仍能列出可加载 Skill（验证：运行 clear 集成或单元测试，观察 active section 为空）
- [ ] 场景 5：一个项目 Skill 白名单引用不存在工具时，OkCode 启动失败并明确提示 Skill 名称和缺失工具名（验证：运行 CLI 启动装配测试，期望返回配置/启动错误而不是进入 REPL）
- [ ] 场景 6：目录型 Skill 激活后，其专属脚本工具只在该 Skill 可见工具集合里出现，普通对话默认工具列表不包含该专属工具（验证：运行目录型 Skill 工具可见性测试，比较激活前后 ProviderRequest.tools）

## spec 验收覆盖

| spec AC | checklist 覆盖 |
|---------|----------------|
| AC1 | 实现完整性：单文件 Skill 可解析；frontmatter 错误路径 |
| AC2 | 实现完整性：目录型 Skill 和专属工具 |
| AC3 | 发现与覆盖：三级覆盖和被覆盖隐藏 |
| AC4 | 发现与覆盖：非法 Skill 不阻断整体 |
| AC5 | 启动与工具白名单：缺失工具启动失败 |
| AC6 | 启动与工具白名单：启动期只注入元数据 |
| AC7 | LoadSkill 与激活快照：按需加载完整指令 |
| AC8 | LoadSkill 与激活快照：多个 Skill 同时激活 |
| AC9 | 执行模式：shared 模式主历史提交 |
| AC10 | 执行模式：isolated 模式摘要回流 |
| AC11 | 执行模式：独立历史范围受控 |
| AC12 | 启动与工具白名单：工具集合收窄 |
| AC13 | `/skill` 命令与终端：列表命令可用 |
| AC14 | LoadSkill 与激活快照：热更新保留快照 |
| AC15 | `/skill` 命令与终端：`/clear` 清理激活状态 |
| AC16 | 实现完整性：内置样板走统一机制 |
| AC17 | 编译与测试：全量测试、Ruff、diff 检查 |

