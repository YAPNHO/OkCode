# OkCode 第十阶段：Skill 系统 Tasks

> 本任务拆解以已批准的 spec.md 和 plan.md 为实现基线。开发开始前必须继续完成并审批 checklist.md；审批前禁止编写实现代码。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/okcode/skills/__init__.py` | 导出 Skill 系统公共接口 |
| 新建 | `src/okcode/skills/models.py` | Skill 枚举、数据结构、异常、事件模型 |
| 新建 | `src/okcode/skills/frontmatter.py` | Markdown frontmatter 解析、正文扫描、占位符替换 |
| 新建 | `src/okcode/skills/discovery.py` | 三级目录扫描、单文件/目录型 Skill 发现、覆盖规则 |
| 新建 | `src/okcode/skills/catalog.py` | SkillCatalog、热更新、完整定义加载、白名单校验 |
| 新建 | `src/okcode/skills/activation.py` | 激活快照、提示词渲染、工具白名单合并、模型覆盖校验 |
| 新建 | `src/okcode/skills/tools.py` | 系统级 `load_skill` 工具、目录型 Skill 脚本工具 |
| 新建 | `src/okcode/skills/runner.py` | 独立模式临时对话执行与摘要回流 |
| 新建 | `src/okcode/skills/builtin/commit/SKILL.md` | 内置 commit Skill |
| 新建 | `src/okcode/skills/builtin/review/SKILL.md` | 内置 review Skill |
| 新建 | `src/okcode/skills/builtin/test/SKILL.md` | 内置 test Skill |
| 修改 | `src/okcode/models.py` | 新增 Skill 列表事件，ProviderRequest 支持模型覆盖 |
| 修改 | `src/okcode/providers/openai.py` | 使用 ProviderRequest 的模型覆盖 |
| 修改 | `src/okcode/providers/anthropic.py` | 使用 ProviderRequest 的模型覆盖 |
| 修改 | `src/okcode/tools/registry.py` | 支持按名称返回工具定义、检查工具存在、注册专属工具 |
| 修改 | `src/okcode/prompt/sections.py` | 新增“可用 Skill”动态提示区块 |
| 修改 | `src/okcode/prompt/builder.py` | 接入 available_skills optional section |
| 修改 | `src/okcode/prompt/runtime.py` | 注入可用 Skill 和已激活 Skill 渲染器 |
| 修改 | `src/okcode/conversation.py` | 接入 SkillRuntime、工具白名单收窄、清空激活状态 |
| 修改 | `src/okcode/commands/models.py` | CommandContext 注入 SkillRuntime |
| 修改 | `src/okcode/commands/handlers.py` | 新增 `/skill` 命令处理函数 |
| 修改 | `src/okcode/commands/defaults.py` | 注册 `/skill` 内置命令 |
| 修改 | `src/okcode/terminal.py` | 渲染 Skill 列表、解析 issue 和激活状态 |
| 修改 | `src/okcode/cli.py` | 启动期 Skill 发现、白名单校验、LoadSkill 注册与依赖注入 |
| 新建 | `tests/unit/test_skills_frontmatter.py` | frontmatter、正文、占位符测试 |
| 新建 | `tests/unit/test_skills_discovery.py` | 目录扫描、覆盖、失败隔离测试 |
| 新建 | `tests/unit/test_skills_catalog.py` | catalog、refresh、白名单校验测试 |
| 新建 | `tests/unit/test_skills_activation.py` | 激活快照、工具集合、模型覆盖测试 |
| 新建 | `tests/unit/test_skills_load_tool.py` | LoadSkill shared 模式、错误路径、专属工具注册测试 |
| 新建 | `tests/unit/test_skills_runner.py` | isolated 模式历史范围、摘要回流、失败摘要测试 |
| 修改 | `tests/unit/test_prompt_builder.py` | 可用 Skill / 已激活 Skill 提示词断言 |
| 修改 | `tests/unit/test_prompt_runtime.py` | RuntimePromptContextFactory Skill 渲染器测试 |
| 修改 | `tests/unit/test_conversation.py` | Skill 白名单工具范围、reset 清激活状态测试 |
| 修改 | `tests/unit/test_commands_handlers.py` | `/skill` 命令输出测试 |
| 修改 | `tests/unit/test_commands_dispatcher.py` | `/skill` 分发回归测试 |
| 修改 | `tests/unit/test_terminal.py` | SkillListEvent 渲染测试 |
| 修改 | `tests/unit/test_app.py` | App 命令上下文注入测试 |
| 修改 | `tests/unit/test_cli.py` | 启动装配、白名单失败、LoadSkill 注册测试 |
| 修改 | `tests/integration/test_tool_turn.py` | LoadSkill 工具调用端到端回归 |

## T1：定义 Skill 核心模型

**文件：** `src/okcode/skills/__init__.py`、`src/okcode/skills/models.py`、`src/okcode/models.py`  
**依赖：** 无  
**步骤：**
1. 创建 `okcode.skills` 包并导出后续模块会用到的公共类型。
2. 在 `models.py` 定义 `SkillSourceKind`、`SkillExecutionMode`、`SkillHistoryMode`。
3. 定义 `SkillMetadata`、`SkillDefinition`、`SkillParseIssue`、`SkillRoots`、`SkillActivation`、`SkillToolManifest`。
4. 定义 Skill 相关异常：`SkillError`、`SkillParseError`、`SkillArgumentError`、`SkillValidationError`。
5. 在 `src/okcode/models.py` 新增 `SkillListEntry`、`SkillListEvent`，并把它加入 `TurnEvent` 联合类型。

**验证：** 运行 `uv run pytest tests/unit/test_cli.py -q -k "no tests"`，期望 pytest 能导入项目且没有语法错误；若该筛选无测试，退出码仍为 5 可接受，但不能出现 import error。

## T2：实现 frontmatter 基础解析

**文件：** `src/okcode/skills/frontmatter.py`、`tests/unit/test_skills_frontmatter.py`  
**依赖：** T1  
**步骤：**
1. 实现入口 Markdown 拆分：文件必须以 `---` frontmatter 开头，第二个 `---` 后是正文。
2. 使用 `yaml.safe_load` 解析 frontmatter，并校验根节点是对象。
3. 校验字段：`name`、`description`、`tools`、`mode`、`history` 必填，`model` 可选。
4. 把 `mode` 映射到 `SkillExecutionMode`，把 `history` 映射到 `SkillHistoryMode`。
5. 对 YAML 语法错误、缺字段、字段类型错误、正文为空分别抛出 `SkillParseError`。
6. 添加单元测试覆盖合法 frontmatter、缺 frontmatter、YAML 错误、缺字段、正文为空。

**验证：** 运行 `uv run pytest tests/unit/test_skills_frontmatter.py -q`，期望全部通过。

## T3：实现占位符提取与渲染

**文件：** `src/okcode/skills/frontmatter.py`、`tests/unit/test_skills_frontmatter.py`  
**依赖：** T2  
**步骤：**
1. 实现 `extract_placeholders(body)`，识别 `{{name}}` 占位符并稳定排序去重。
2. 限制占位符名称只能包含字母、数字、下划线和短横线。
3. 实现 `render_body(body, arguments)`，用 `arguments` 替换正文中的占位符。
4. 缺少占位符参数时抛出 `SkillArgumentError`，错误信息包含缺失参数名。
5. 对未被模板引用的额外参数，在正文末尾追加“用户传入参数”JSON 区块。
6. 添加测试覆盖重复占位符、缺参、额外参数和非法占位符。

**验证：** 运行 `uv run pytest tests/unit/test_skills_frontmatter.py -q`，期望全部通过。

## T4：实现 SkillRoots 和文件发现

**文件：** `src/okcode/skills/discovery.py`、`tests/unit/test_skills_discovery.py`  
**依赖：** T2  
**步骤：**
1. 实现 `SkillRoots.for_workspace(workspace_root)`，返回内置、用户、项目三层路径。
2. 实现扫描单文件 Skill：匹配每层目录下的 `*.md`。
3. 实现扫描目录型 Skill：匹配包含 `SKILL.md` 的一级子目录。
4. 发现阶段只读取 frontmatter 和正文是否存在，不保留完整正文。
5. 路径读取失败或单个 Skill 解析失败时生成 `SkillParseIssue`，不阻断其他 Skill。
6. 添加测试覆盖三层路径、单文件发现、目录型发现、非法文件跳过。

**验证：** 运行 `uv run pytest tests/unit/test_skills_discovery.py -q`，期望全部通过。

## T5：实现来源覆盖和同源冲突

**文件：** `src/okcode/skills/discovery.py`、`tests/unit/test_skills_discovery.py`  
**依赖：** T4  
**步骤：**
1. 对 Skill 名称做大小写不敏感归一。
2. 同一来源出现同名 Skill 时抛出 `SkillValidationError`，错误包含两个路径。
3. 不同来源同名时按 PROJECT > USER > BUILTIN 选择有效 Skill。
4. 保存被覆盖版本到 discovery result，供 catalog 诊断使用。
5. 有效 Skill 按来源优先级和名称稳定排序。
6. 添加测试覆盖同源冲突、跨源覆盖和排序稳定性。

**验证：** 运行 `uv run pytest tests/unit/test_skills_discovery.py -q`，期望全部通过。

## T6：解析目录型 Skill 专属工具 manifest

**文件：** `src/okcode/skills/discovery.py`、`src/okcode/skills/models.py`、`tests/unit/test_skills_discovery.py`  
**依赖：** T4  
**步骤：**
1. 定义目录型工具 manifest 文件位置为 `tools/tools.yaml`。
2. 解析 manifest 中的工具数组，字段包含 local_name、description、schema_path、script_path、timeout_seconds、safety、permission_target。
3. 校验 schema 和 script 路径必须位于 Skill 包目录内。
4. 将每个专属工具暴露名生成为 `skill__{skill_name}__{local_name}`。
5. manifest 缺字段、非法 safety、路径越界或 schema 缺失时跳过该 Skill 并记录 issue。
6. 添加测试覆盖合法 manifest、路径越界、缺字段和无 manifest 的目录型 Skill。

**验证：** 运行 `uv run pytest tests/unit/test_skills_discovery.py -q`，期望全部通过。

## T7：实现 SkillCatalog 和启动期白名单校验

**文件：** `src/okcode/skills/catalog.py`、`tests/unit/test_skills_catalog.py`  
**依赖：** T5、T6  
**步骤：**
1. 实现 `SkillCatalog` 保存 effective、overridden、issues、roots、known_tool_names。
2. 实现 `list()`、`get(name)`、`issues_for_display()`。
3. 实现 `validate_skill_catalog`，用普通工具名、`load_skill` 和专属工具名校验 `tools` 白名单。
4. 白名单引用不存在工具时抛出 `SkillValidationError`，错误包含 Skill 名称和工具名。
5. 实现 `refresh()`，重新扫描并更新有效列表，但不接触激活快照。
6. 添加测试覆盖白名单合法、缺失工具失败、refresh 新增/删除/解析失败。

**验证：** 运行 `uv run pytest tests/unit/test_skills_catalog.py -q`，期望全部通过。

## T8：实现完整 SkillDefinition 加载

**文件：** `src/okcode/skills/catalog.py`、`tests/unit/test_skills_catalog.py`  
**依赖：** T7  
**步骤：**
1. 实现 `load_definition(name)`，按当前 effective 元数据读取入口 Markdown 完整正文。
2. 调用占位符提取函数生成 placeholders。
3. 重新校验文件仍然存在、正文仍非空、frontmatter 名称仍匹配有效元数据。
4. 读取目录型 Skill 的专属工具定义，保留 manifest 中解析出的 schema 路径和脚本路径。
5. 目标 Skill 不存在、热更新后解析失败或文件删除时抛出可诊断异常。
6. 添加测试覆盖成功加载、文件删除、正文变空和名称变化。

**验证：** 运行 `uv run pytest tests/unit/test_skills_catalog.py -q`，期望全部通过。

## T9：实现激活快照存储

**文件：** `src/okcode/skills/activation.py`、`tests/unit/test_skills_activation.py`  
**依赖：** T3、T8  
**步骤：**
1. 实现 `SkillActivationStore.activate(definition, arguments)`，渲染 SOP 并创建快照。
2. 同名 Skill 再次激活时用最新快照替换旧快照，并保持激活顺序可预测。
3. 实现 `active()`、`clear()`、`render_active_section()`。
4. 激活区块按名称或激活顺序稳定渲染，每个 Skill 有清晰标题、来源、版本和完整 SOP。
5. 确保热更新不会修改已存在快照。
6. 添加测试覆盖首次激活、同名替换、多个 Skill 顺序、clear 和渲染内容。

**验证：** 运行 `uv run pytest tests/unit/test_skills_activation.py -q`，期望全部通过。

## T10：实现可用工具集合计算和模型覆盖校验

**文件：** `src/okcode/skills/activation.py`、`tests/unit/test_skills_activation.py`  
**依赖：** T9  
**步骤：**
1. 实现 `visible_tool_names(default_names, load_skill_name)`。
2. 无激活 Skill 时返回默认工具名加 `load_skill`。
3. 有激活 Skill 时返回所有激活 Skill 白名单并集、专属工具名和 `load_skill`。
4. 保证返回值稳定排序，并去重。
5. 实现 `model_override()` 和 `assert_model_compatible(new_model)`。
6. 添加测试覆盖无激活、有激活、多个 Skill 并集、专属工具、模型冲突。

**验证：** 运行 `uv run pytest tests/unit/test_skills_activation.py -q`，期望全部通过。

## T11：实现目录型 Skill 脚本工具

**文件：** `src/okcode/skills/tools.py`、`tests/unit/test_skills_load_tool.py`  
**依赖：** T6  
**步骤：**
1. 实现 `SkillScriptTool` 并满足现有 `Tool` Protocol。
2. 读取 manifest schema JSON 作为 ToolDefinition 的 input_schema。
3. 使用当前 Python 解释器启动脚本，把工具参数 JSON 写入 stdin。
4. 解析 stdout JSON 为 `ToolOutput(content, data, truncated)`。
5. 把非零退出、超时、非法 JSON、缺 content、data 非对象转为 `ToolFailure`。
6. 添加测试覆盖成功输出、脚本失败、非法 JSON、超时和路径限制。

**验证：** 运行 `uv run pytest tests/unit/test_skills_load_tool.py -q -k "script_tool"`，期望全部通过。

## T12：扩展 ToolRegistry 支持按名称取定义

**文件：** `src/okcode/tools/registry.py`、`tests/unit/test_tools_registry.py`  
**依赖：** T1  
**步骤：**
1. 新增 `has(name)` 或等价方法，用于白名单校验和启动装配。
2. 新增 `definitions_by_names(names)`，按输入名称集合返回对应 ToolDefinition。
3. 缺失名称时抛出 ValueError 或返回可诊断错误，避免静默漏工具。
4. 保持现有 `definitions()` 和 `definitions_by_safety()` 行为不变。
5. 添加测试覆盖按名称取定义、排序、缺失名称和原有注册冲突。
6. 运行现有工具注册测试，确认无回归。

**验证：** 运行 `uv run pytest tests/unit/test_tools_registry.py -q`，期望全部通过。

## T13：实现 LoadSkillTool 共享模式

**文件：** `src/okcode/skills/tools.py`、`tests/unit/test_skills_load_tool.py`  
**依赖：** T7、T8、T9、T11、T12  
**步骤：**
1. 定义系统级工具 `load_skill`，safety 为 READ_ONLY，permission_target 为 NONE。
2. 实现输入 schema：`name` 必填，`arguments` 和 `history_override` 可选。
3. 执行时先调用 catalog.refresh，再 load_definition。
4. 对 shared 模式调用 activation_store.activate，并注册该 Skill 的专属工具。
5. 返回结构化 ToolOutput，包含激活 Skill 名称、版本、执行模式、可见工具。
6. 添加测试覆盖正常激活、未知 Skill、缺占位符参数、重复激活替换旧快照。

**验证：** 运行 `uv run pytest tests/unit/test_skills_load_tool.py -q -k "load_skill and shared"`，期望全部通过。

## T14：实现独立模式历史选择

**文件：** `src/okcode/skills/runner.py`、`tests/unit/test_skills_runner.py`  
**依赖：** T1  
**步骤：**
1. 定义 `SkillRunResult`，包含 success、summary、error_message。
2. 实现 `SkillRunner` 的历史选择函数。
3. NONE 返回空历史。
4. RECENT 返回最近合法消息片段，不能拆开 assistant tool_calls 和 tool result。
5. SUMMARY 从 context manager 的系统摘要指令中提取摘要上下文。
6. ALL_SAFE 在预算函数允许范围内返回尽可能多的合法历史。
7. 添加测试覆盖四种 history mode 和工具消息配对边界。

**验证：** 运行 `uv run pytest tests/unit/test_skills_runner.py -q -k "history"`，期望全部通过。

## T15：实现独立模式临时对话运行

**文件：** `src/okcode/skills/runner.py`、`tests/unit/test_skills_runner.py`  
**依赖：** T10、T14  
**步骤：**
1. 用当前 SkillActivation 构造只包含该 Skill SOP 的 PromptBuildContext。
2. 计算独立模式可见工具：该 Skill 白名单、专属工具、`load_skill`。
3. 调用 provider.stream 执行临时对话。
4. 复用 ToolExecutor 处理工具调用，但不把临时 messages 写入主 ConversationSession。
5. 最终助手文本返回为 success summary。
6. Provider 停止、工具失败或空回答时返回失败摘要。
7. 添加测试覆盖成功摘要、工具调用、失败摘要、不污染主历史。

**验证：** 运行 `uv run pytest tests/unit/test_skills_runner.py -q`，期望全部通过。

## T16：接入 LoadSkillTool 独立模式

**文件：** `src/okcode/skills/tools.py`、`tests/unit/test_skills_load_tool.py`  
**依赖：** T13、T15  
**步骤：**
1. 在 LoadSkillTool 中识别 isolated 模式。
2. 激活快照后调用 SkillRunner.run。
3. 支持 `history_override` 临时覆盖 history mode。
4. 成功时把隔离摘要写入 ToolOutput。
5. 失败时返回 success=false 的 ToolFailure 或等价结构化错误，保留激活快照行为与 plan 一致。
6. 添加测试覆盖 isolated 成功、history_override、runner 失败、主激活状态。

**验证：** 运行 `uv run pytest tests/unit/test_skills_load_tool.py tests/unit/test_skills_runner.py -q`，期望全部通过。

## T17：新增内置 commit/review/test Skill

**文件：** `src/okcode/skills/builtin/commit/SKILL.md`、`src/okcode/skills/builtin/review/SKILL.md`、`src/okcode/skills/builtin/test/SKILL.md`、`tests/unit/test_skills_catalog.py`  
**依赖：** T7  
**步骤：**
1. 编写 commit Skill frontmatter，白名单至少包含读取、搜索、命令相关工具，mode 使用 shared。
2. 编写 review Skill frontmatter，聚焦代码审查 SOP，mode 使用 shared。
3. 编写 test Skill frontmatter，聚焦运行测试、定位失败、修复建议或修复执行，mode 使用 shared。
4. 正文使用中文 SOP，明确工具使用步骤和输出格式。
5. 确认三个 Skill 不注册斜杠命令，只能通过 `load_skill` 激活。
6. 添加测试确认内置三项能被 catalog 发现和加载。

**验证：** 运行 `uv run pytest tests/unit/test_skills_catalog.py -q -k "builtin"`，期望全部通过。

## T18：扩展提示词 optional sections

**文件：** `src/okcode/prompt/sections.py`、`src/okcode/prompt/builder.py`、`tests/unit/test_prompt_builder.py`  
**依赖：** T9  
**步骤：**
1. 在 `PromptOptionalSections` 中新增 `available_skills` 字段。
2. 在 `optional_sections()` 中新增“可用 Skill”区块，优先级放在自定义指令之后、已激活 Skill 之前。
3. 保持“已激活的 Skill”区块继续使用完整快照内容。
4. 确认空内容不产生区块。
5. 更新 prompt builder 的 `_section_kind` 映射。
6. 添加测试断言稳定系统提示不包含完整 Skill，动态提示包含可用 Skill 和已激活 Skill。

**验证：** 运行 `uv run pytest tests/unit/test_prompt_builder.py -q`，期望全部通过。

## T19：扩展 RuntimePromptContextFactory

**文件：** `src/okcode/prompt/runtime.py`、`tests/unit/test_prompt_runtime.py`  
**依赖：** T18  
**步骤：**
1. RuntimePromptContextFactory 构造函数新增 `available_skills_provider` 和 `active_skills_provider` 可选参数。
2. 默认 provider 返回空字符串，保持旧调用方兼容。
3. 每轮构建 PromptBuildContext 时调用 provider 获取最新 Skill 文本。
4. 保持长期记忆读取逻辑不变。
5. 添加测试覆盖默认空 Skill、可用 Skill、已激活 Skill 动态更新。
6. 确认 existing prompt runtime 测试仍通过。

**验证：** 运行 `uv run pytest tests/unit/test_prompt_runtime.py -q`，期望全部通过。

## T20：接入 ConversationSession 工具可见范围

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T10、T12、T18  
**步骤：**
1. ConversationSession 构造函数接收 SkillActivationStore 或 SkillRuntime，可为空以保持测试兼容。
2. 新增 `visible_tools_for(mode, tool_scope)` 集中计算 DEFAULT/PLAN 与 Skill 白名单后的工具定义。
3. 无激活 Skill 时沿用现有 DEFAULT 全量工具、PLAN 只读工具，并额外包含 `load_skill`。
4. 有激活 Skill 时使用 activation_store.visible_tool_names 计算工具名，再从 ToolRegistry 取 ToolDefinition。
5. `_build_normal_request` 使用收窄后的 visible_tools 构建 prompt 和 ProviderRequest。
6. 添加测试覆盖无 Skill、有 Skill、PLAN 模式、白名单外工具不可见、load_skill 始终可见。

**验证：** 运行 `uv run pytest tests/unit/test_conversation.py -q -k "skill or visible_tools or plan"`，期望全部通过。

## T21：清空对话时清理 Skill 激活状态

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T20  
**步骤：**
1. 在 `reset_session()` 中调用 activation_store.clear()。
2. 保持原有 messages、saved_plan、token、turn_count、journal 轮换逻辑不变。
3. 确保 context manager restore_history 仍按原逻辑执行。
4. 添加测试：激活 Skill 后 reset_session，下一轮 active section 为空。
5. 添加测试：reset_session 不删除 catalog 可用 Skill 元数据。
6. 运行已有 clear/session 相关测试确认无回归。

**验证：** 运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_commands_handlers.py -q -k "clear or reset or skill"`，期望全部通过。

## T22：ProviderRequest 支持模型覆盖

**文件：** `src/okcode/models.py`、`src/okcode/providers/openai.py`、`src/okcode/providers/anthropic.py`、`tests/unit/test_models.py`、`tests/integration/test_openai_sse.py`、`tests/integration/test_anthropic_sse.py`  
**依赖：** T10  
**步骤：**
1. 在 ProviderRequest 增加 `model_override: str | None = None`。
2. ConversationSession 构建请求时从 activation_store.model_override() 读取覆盖模型。
3. OpenAI provider 构造请求 payload 时优先使用 model_override。
4. Anthropic provider 构造请求 payload 时优先使用 model_override。
5. 添加模型覆盖测试，确认无覆盖时保持原配置模型。
6. 添加不同激活 Skill model 冲突测试，确认 LoadSkill 返回失败且不污染激活状态。

**验证：** 运行 `uv run pytest tests/unit/test_models.py tests/integration/test_openai_sse.py tests/integration/test_anthropic_sse.py tests/unit/test_skills_activation.py -q`，期望全部通过。

## T23：实现 `/skill` 命令模型和 handler

**文件：** `src/okcode/commands/models.py`、`src/okcode/commands/handlers.py`、`tests/unit/test_commands_handlers.py`  
**依赖：** T7、T9、T1  
**步骤：**
1. CommandContext 新增 skill_runtime 或 catalog/activation_store 访问字段。
2. 实现 `skill_command`，执行前调用 catalog.refresh。
3. 构造 SkillListEvent，包含可加载 Skill、issue 和已激活名称。
4. Skill refresh 出现启动级错误时返回 error 级 CommandNotice 或 SkillListEvent issue。
5. 普通 Skill 不进入 CommandRegistry。
6. 添加测试覆盖空列表、内置列表、解析 issue、已激活标记。

**验证：** 运行 `uv run pytest tests/unit/test_commands_handlers.py -q -k "skill"`，期望全部通过。

## T24：注册 `/skill` 命令并保持命令补全

**文件：** `src/okcode/commands/defaults.py`、`tests/unit/test_commands_registry.py`、`tests/unit/test_commands_dispatcher.py`  
**依赖：** T23  
**步骤：**
1. 在默认命令注册中心新增 `skill`，kind 为 LOCAL。
2. description 写成“列出可加载和已激活的 Skill”。
3. usage 写成 `/skill`，argument_hint 为空。
4. 确认 `/help` 能显示 `/skill`，补全候选包含 `/skill`。
5. 确认普通 Skill 名称不进入 CommandRegistry。
6. 更新默认命令数量相关测试，从 12 改为 13。

**验证：** 运行 `uv run pytest tests/unit/test_commands_registry.py tests/unit/test_commands_dispatcher.py tests/unit/test_commands_handlers.py -q`，期望全部通过。

## T25：终端渲染 Skill 列表事件

**文件：** `src/okcode/terminal.py`、`tests/unit/test_terminal.py`  
**依赖：** T1、T23  
**步骤：**
1. 在 TerminalUI.render_event 中处理 SkillListEvent。
2. 渲染可加载 Skill：名称、来源、激活标记、一句话说明。
3. 有 issue 时在列表下方渲染诊断信息。
4. 保持现有命令帮助、状态栏、普通事件渲染不变。
5. 添加测试覆盖空列表、有激活、有 issue 和 ANSI/可见文本顺序。
6. 确认状态栏不显示 Skill 列表。

**验证：** 运行 `uv run pytest tests/unit/test_terminal.py -q -k "skill or command"`，期望全部通过。

## T26：CLI 启动装配 Skill 系统

**文件：** `src/okcode/cli.py`、`tests/unit/test_cli.py`  
**依赖：** T7、T13、T15、T17、T19、T20、T23、T24  
**步骤：**
1. 在默认工具和 MCP 工具注册完成后创建 SkillRoots 和 SkillCatalog。
2. 使用当前工具名、`load_skill` 和专属工具名校验白名单。
3. 创建 SkillActivationStore 和 SkillRunner。
4. 注册 LoadSkillTool 到 ToolRegistry。
5. 构造 RuntimePromptContextFactory 时传入可用 Skill 和已激活 Skill 渲染 provider。
6. 构造 CommandRegistry 时包含 `/skill`。
7. 构造 OkCodeApp/CommandContext 时注入 SkillRuntime。
8. 添加测试覆盖启动成功、白名单缺失工具启动失败、LoadSkill 已注册、`/skill` 已注册。

**验证：** 运行 `uv run pytest tests/unit/test_cli.py -q`，期望全部通过。

## T27：App 命令上下文传递 SkillRuntime

**文件：** `src/okcode/app.py`、`tests/unit/test_app.py`  
**依赖：** T23、T26  
**步骤：**
1. OkCodeApp 构造函数接收 skill_runtime 或通过 conversation 暴露。
2. `_handle_input` 创建 CommandContext 时填入 SkillRuntime。
3. 保持普通文本、空输入、退出、恢复会话、clear 路径不变。
4. 添加测试确认 `/skill` 通过 dispatcher 调用 handler。
5. 添加测试确认普通输入仍进入 ConversationSession.stream_user_message。
6. 运行现有 app 测试确认无回归。

**验证：** 运行 `uv run pytest tests/unit/test_app.py -q`，期望全部通过。

## T28：集成 LoadSkill 到 Agent Loop

**文件：** `src/okcode/conversation.py`、`tests/integration/test_tool_turn.py`  
**依赖：** T13、T20、T26  
**步骤：**
1. 用现有集成测试替身构造一个会调用 `load_skill` 的 Provider 响应。
2. 确认 ToolExecutor 执行 LoadSkillTool 并写回工具结果。
3. 确认下一次 Provider 请求包含已激活 Skill 完整 SOP。
4. 确认最终消息成功提交到主历史。
5. 添加白名单外工具不可见的请求断言。
6. 确认未知工具处理逻辑不被 Skill 改动破坏。

**验证：** 运行 `uv run pytest tests/integration/test_tool_turn.py -q -k "skill or tool"`，期望全部通过。

## T29：验证热更新行为

**文件：** `tests/unit/test_skills_catalog.py`、`tests/unit/test_skills_load_tool.py`、`tests/unit/test_commands_handlers.py`  
**依赖：** T7、T9、T13、T23  
**步骤：**
1. 测试 `/skill` 或 handler refresh 后能看到新增 Skill。
2. 测试修改 Skill description 后 `/skill` 显示新说明。
3. 测试删除 Skill 后无法新加载。
4. 测试已激活 Skill 在源文件修改后仍保留旧快照。
5. 测试再次 LoadSkill 同名 Skill 后替换为新快照。
6. 测试重新加载时文件已删除或解析失败，旧快照保留并返回可诊断错误。

**验证：** 运行 `uv run pytest tests/unit/test_skills_catalog.py tests/unit/test_skills_load_tool.py tests/unit/test_commands_handlers.py -q -k "refresh or hot or reload"`，期望全部通过。

## T30：补全错误路径和诊断信息测试

**文件：** `tests/unit/test_skills_frontmatter.py`、`tests/unit/test_skills_discovery.py`、`tests/unit/test_skills_catalog.py`、`tests/unit/test_skills_load_tool.py`  
**依赖：** T2-T16  
**步骤：**
1. 检查所有 Skill 解析错误都包含路径或来源类别。
2. 检查白名单缺失工具错误包含 Skill 名称和工具名。
3. 检查 LoadSkill 未找到 Skill、缺参数、模型冲突、专属工具加载失败都有明确错误。
4. 检查目录型 Skill manifest 路径越界错误可诊断。
5. 检查单个失败 Skill 不影响其他合法 Skill。
6. 删除测试里的模糊断言，使用具体错误文本片段。

**验证：** 运行 `uv run pytest tests/unit/test_skills_frontmatter.py tests/unit/test_skills_discovery.py tests/unit/test_skills_catalog.py tests/unit/test_skills_load_tool.py -q`，期望全部通过。

## T31：完整单元测试回归

**文件：** 多个测试文件  
**依赖：** T1-T30  
**步骤：**
1. 运行全部 unit tests。
2. 如果失败，先根据失败定位修复对应任务代码。
3. 确认命令系统、提示词、工具系统、权限系统、上下文管理、会话系统没有回归。
4. 确认新增 Skill 测试没有依赖真实网络。
5. 修复所有警告或不稳定断言。
6. 记录最终通过数量。

**验证：** 运行 `uv run pytest tests/unit -q`，期望全部通过。

## T32：完整集成与静态检查

**文件：** 全项目  
**依赖：** T31  
**步骤：**
1. 运行集成测试。
2. 运行全量 pytest。
3. 运行 Ruff 格式检查。
4. 运行 Ruff lint。
5. 运行 `git diff --check`。
6. 如有失败，修复后重新运行对应命令。

**验证：** 依次运行 `uv run pytest -q`、`uv run ruff format --check .`、`uv run ruff check .`、`git diff --check`，期望全部通过。

## 执行顺序

```text
T1
  → T2 → T3
  → T4 → T5 → T6 → T7 → T8
  → T9 → T10
  → T11 → T12 → T13 → T14 → T15 → T16
  → T17
  → T18 → T19 → T20 → T21 → T22
  → T23 → T24 → T25 → T26 → T27
  → T28 → T29 → T30
  → T31 → T32
```

## 自检

- plan 覆盖：plan.md 中的模型、解析、发现、catalog、activation、tools、runner、builtin、prompt、conversation、commands、terminal、cli 都有对应任务。
- 验证完整性：每个任务都有具体 pytest 或项目检查命令。
- 依赖链：先建模型和解析，再接 catalog/activation/tools，最后接入会话、命令、CLI 和回归测试。
- 范围控制：不实现市场分发、版本管理、跨机器同步、可视化编辑器或后台任务系统。
- 用户决策对齐：普通 Skill 不注册为斜杠短命令，`/skill` 只负责列表，完整 SOP 统一通过 `load_skill` 激活。

