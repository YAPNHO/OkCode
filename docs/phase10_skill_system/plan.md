# OkCode 第十阶段：Skill 系统 Plan

## 架构概览

本阶段新增独立的 `okcode.skills` 包，负责 Skill 定义、发现、覆盖、解析错误隔离、LoadSkill 激活、激活快照、专属工具装配和 Skill 执行模式。Skill 系统不替代现有命令系统、工具系统、权限系统或提示词管线，而是接入这些既有边界。

启动流程新增一个 Skill 装配阶段。CLI 在默认工具、MCP 工具发现完成后，创建 SkillCatalog 并扫描内置、用户、项目三级目录。扫描阶段只读取 frontmatter 元数据和正文是否存在的轻量信号，不把完整 SOP 注入上下文。完成发现后，系统用当前已注册工具名加每个目录型 Skill 的专属工具名校验所有 Skill 的工具白名单；白名单引用不存在工具时启动失败。通过校验后，系统注册一个系统级 `load_skill` 工具，并把 SkillCatalog 与 SkillRuntime 注入 ConversationSession、RuntimePromptContextFactory 和命令上下文。

提示词管线新增“可用 Skill”和“已激活的 Skill”两个动态区块。启动期和普通请求只展示可用 Skill 的名称与一句话说明；LoadSkill 激活某个 Skill 后，SkillRuntime 生成激活快照，后续每轮请求都把快照中的完整 SOP、参数替换结果、来源和版本号渲染到“已激活的 Skill”区块。激活快照固定使用激活时版本；热更新只影响后续 LoadSkill 结果，不会修改已激活快照，直到 `/clear` 或显式重新加载同名 Skill。

工具可见范围由 ConversationSession 在每次构建 ProviderRequest 时统一计算。没有激活 Skill 时沿用现有 DEFAULT/PLAN 工具范围，并额外保证 `load_skill` 可见；存在激活 Skill 时，当前模型可见工具收窄为“所有已激活 Skill 白名单的并集 + 已激活 Skill 专属工具 + `load_skill`”。该规则不会回退到全量工具，因此多个 Skill 同时激活只会扩大到显式白名单并集，不会意外暴露全部工具。ToolExecutor 仍然以完整 ToolRegistry 为执行边界，权限确认、黑名单、工作区限制和结构化工具结果全部沿用现有逻辑。

命令系统只新增一个内置 `/skill` 命令。`/skill` 是纯本地命令，用于列出当前可加载 Skill、来源优先级、说明、解析警告和已激活状态。普通 Skill 不注册为斜杠命令，不占用命令命名空间，也不通过命令系统直接触发。用户自然语言要求使用某个 Skill 时，由模型根据启动期 Skill 列表调用 `load_skill` 工具完成按需激活。

目录型 Skill 的专属工具采用“声明 + 脚本”模型。目录包中的工具 manifest 声明工具元数据、schema、脚本路径、安全类别和权限目标；运行时把它包装成 ToolRegistry 可执行的 Tool。专属工具只在对应 Skill 激活后进入该 Skill 的可见工具集合，但工具对象可以在注册表中按隐藏方式准备好，以便 ToolExecutor 统一执行。

两种执行模式由 LoadSkillTool 分流。共享模式下，LoadSkill 只激活快照并返回结构化工具结果，当前 Agent Loop 的下一次模型请求会看到完整 SOP 并继续在主会话中工作。独立模式下，LoadSkillTool 调用 SkillRunner 创建隔离临时对话，按 Skill 的历史范围配置选择输入历史，使用同一套 Provider、ToolExecutor、提示词构建和工具可见范围运行，完成后只把摘要、关键结果或失败原因作为 LoadSkill 工具结果回到主历史。

## 核心数据结构

### SkillSourceKind

定义在 `okcode.skills.models` 中，取值为 BUILTIN、USER、PROJECT。

优先级从低到高为 BUILTIN < USER < PROJECT。覆盖规则只在不同来源之间发生；同一来源出现同名 Skill 是内部冲突，启动失败并指出冲突路径。

### SkillExecutionMode

定义在 `okcode.skills.models` 中，取值为 SHARED、ISOLATED。

- SHARED：LoadSkill 激活快照，当前主 Agent Loop 继续运行，结果写入主历史。
- ISOLATED：LoadSkill 激活快照后由 SkillRunner 运行隔离临时对话，主历史只保留 LoadSkill 工具结果和最终助手回答。

### SkillHistoryMode

定义在 `okcode.skills.models` 中，取值为 NONE、RECENT、SUMMARY、ALL_SAFE。

- NONE：独立执行不带入主历史。
- RECENT：带入最近已完成的安全消息片段，默认上限由 SkillRuntime 常量控制。
- SUMMARY：只带入当前上下文摘要系统指令和必要环境事实。
- ALL_SAFE：在上下文预算和工具配对合法的前提下带入尽可能多的已完成历史。

该字段只影响 ISOLATED 模式。SHARED 模式解析但不使用它。

### SkillMetadata

字段：

- name: str，唯一 Skill 名称，大小写不敏感归一，原始大小写用于展示。
- description: str，一句话说明。
- allowed_tools: tuple[str, ...]，普通工具白名单，允许为空。
- execution_mode: SkillExecutionMode。
- history_mode: SkillHistoryMode。
- model: str | None，可选模型覆盖。
- source: SkillSourceKind。
- source_path: Path，单文件路径或目录路径。
- entry_path: Path，入口 Markdown 路径。
- package_dir: Path | None，目录型 Skill 的根目录。
- version_id: str，基于入口 Markdown 和工具 manifest 的 mtime、size、路径生成的稳定版本标识。
- has_body: bool，发现阶段确认正文存在，但不保留完整正文。
- dedicated_tools: tuple[SkillToolManifest, ...]，目录型 Skill 声明的专属工具。

### SkillDefinition

字段：

- metadata: SkillMetadata。
- body: str，LoadSkill 阶段读取的完整 SOP 正文。
- placeholders: tuple[str, ...]，从正文中提取的 `{{name}}` 占位符名。
- dedicated_tools: tuple[SkillToolDefinition, ...]，已解析、可注册的专属工具定义。

SkillDefinition 只在 LoadSkill 阶段构造，不进入启动期提示词。

### SkillParseIssue

字段：

- source_path: Path。
- source: SkillSourceKind。
- skill_name: str | None。
- severity: Literal["warning", "error"]。
- message: str。

解析错误用于 `/skill` 展示和测试断言。单个 Skill 解析失败只产生 issue 并跳过该 Skill；白名单缺失工具、同一来源名称冲突和 `/skill` 命令注册冲突属于启动失败，不用 issue 吞掉。

### SkillCatalog

字段：

- effective: dict[str, SkillMetadata]，归一化名称到最终有效元数据。
- overridden: dict[str, tuple[SkillMetadata, ...]]，被高优先级覆盖的低优先级版本。
- issues: tuple[SkillParseIssue, ...]，解析失败和非阻断警告。
- roots: SkillRoots。
- refreshed_at: datetime。

接口：

- list() -> tuple[SkillMetadata, ...]，按来源优先级、名称稳定排序后返回。
- get(name) -> SkillMetadata | None，大小写不敏感查找有效 Skill。
- issues_for_display() -> tuple[SkillParseIssue, ...]。
- refresh(known_tool_names) -> SkillCatalog，重新扫描目录并重新校验。
- load_definition(name) -> SkillDefinition，从当前有效元数据读取完整 SOP 和专属工具。

### SkillRoots

字段：

- builtin: Path，`src/okcode/skills/builtin`。
- user: Path，`Path.home() / ".okcode" / "skills"`。
- project: Path，`workspace.root / ".okcode" / "skills"`。

接口：

- for_workspace(workspace_root: Path) -> SkillRoots。

### SkillFrontmatter

frontmatter 字段名：

```yaml
name: commit
description: 生成提交前检查和提交信息
tools: [read_file, search_code, run_command]
mode: shared
history: recent
model: null
```

字段规则：

- `name` 和 `description` 必填。
- `tools` 必填，必须是字符串列表，可以为空列表。
- `mode` 必填，只能是 `shared` 或 `isolated`。
- `history` 必填，只能是 `none`、`recent`、`summary`、`all_safe`。
- `model` 可选，必须是非空字符串或 null。

### SkillActivation

字段：

- name: str。
- description: str。
- source: SkillSourceKind。
- source_path: Path。
- version_id: str。
- rendered_sop: str，已完成占位符替换的 SOP。
- arguments: Mapping[str, JSONValue]，LoadSkill 传入参数。
- allowed_tools: tuple[str, ...]。
- exposed_dedicated_tool_names: tuple[str, ...]。
- execution_mode: SkillExecutionMode。
- history_mode: SkillHistoryMode。
- model: str | None。

该对象是激活快照。热更新不修改已存在 SkillActivation；重新 LoadSkill 同名 Skill 时，用最新 SkillDefinition 替换该对象。

### SkillActivationStore

职责：维护当前会话已激活 Skill 快照。

接口：

- activate(definition, arguments) -> SkillActivation。
- replace(activation)。
- clear()。
- active() -> tuple[SkillActivation, ...]，按激活顺序返回。
- render_prompt_section() -> str，渲染“已激活的 Skill”动态提示区块。
- visible_tool_names(base_tool_names) -> tuple[str, ...]，计算白名单并集、专属工具和 load_skill。
- model_override() -> str | None，返回唯一模型覆盖；多个不同模型覆盖同时存在时拒绝激活新的冲突 Skill。

### LoadSkillArguments

LoadSkill 工具输入 schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["name"],
  "properties": {
    "name": {"type": "string", "minLength": 1},
    "arguments": {"type": "object", "additionalProperties": true},
    "history_override": {
      "type": ["string", "null"],
      "enum": ["none", "recent", "summary", "all_safe", null]
    }
  }
}
```

- `name`：Skill 名称，大小写不敏感。
- `arguments`：占位符和额外参数来源，默认空对象。
- `history_override`：仅允许独立模式临时覆盖 frontmatter 中的 history。

显式重新加载同名 Skill 不需要额外字段；再次调用 LoadSkill 即表示用当前最新有效定义替换旧快照。

### SkillToolManifest

目录型 Skill 专属工具 manifest 字段：

- local_name: str，目录包内部工具名。
- description: str。
- schema_path: Path。
- script_path: Path。
- timeout_seconds: float。
- safety: ToolSafety。
- permission_target: PermissionTarget。

运行时暴露工具名统一为 `skill__{skill_name}__{local_name}`，避免与全局工具和其他 Skill 专属工具冲突。

### SkillScriptTool

实现 okcode.tools.base.Tool。

定义：

- definition.name 使用 `skill__{skill_name}__{local_name}`。
- definition.description 来自 manifest，并追加“此工具属于 Skill: name”。
- definition.input_schema 来自 schema JSON。
- definition.safety 和 permission_target 来自 manifest。

执行协议：

- 使用当前 Python 解释器运行 manifest 指向的脚本。
- 向 stdin 写入 JSON 参数对象。
- 脚本必须向 stdout 输出 JSON 对象：`content` 必填，`data` 可选，`truncated` 可选。
- 非零退出码、超时、非法 JSON、缺少 content 或 data 非对象都转为 ToolFailure，不让运行时崩溃。

### SkillListEvent

新增 TurnEvent 类型，供 `/skill` 渲染：

- entries: tuple[SkillListEntry, ...]。
- issues: tuple[str, ...]。
- active_names: tuple[str, ...]。

### SkillListEntry

字段：

- name: str。
- description: str。
- source: str。
- active: bool。
- version_id: str。

## 模块设计

### okcode.skills.models

职责：定义 Skill 枚举、数据类、异常和公共类型。

对外接口：SkillSourceKind、SkillExecutionMode、SkillHistoryMode、SkillMetadata、SkillDefinition、SkillParseIssue、SkillCatalogSnapshot、SkillActivation、SkillToolManifest。

依赖：pathlib、dataclasses、okcode.tools.models。

### okcode.skills.frontmatter

职责：解析入口 Markdown 的 YAML frontmatter、字段校验和占位符提取。

对外接口：

- parse_frontmatter(path) -> SkillFrontmatter。
- scan_has_body(path) -> bool。
- extract_placeholders(body) -> tuple[str, ...]。
- render_body(body, arguments) -> str。

占位符语法固定为 `{{name}}`。缺少参数时抛出 SkillArgumentError；额外参数不会丢弃，会追加到 SOP 后的“用户传入参数”JSON 区块，保证模型能看到用户传入但未被模板引用的内容。

### okcode.skills.discovery

职责：扫描三个 Skill 来源目录，识别单文件 Skill 和目录型 Skill，应用覆盖规则并收集解析 issue。

对外接口：

- SkillRoots.for_workspace(workspace_root)。
- discover_skills(roots) -> SkillDiscoveryResult。
- validate_skill_catalog(catalog, known_tool_names) -> None。

发现规则：

- 单文件 Skill：`*.md` 文件。
- 目录型 Skill：目录内必须有 `SKILL.md`。
- 目录型工具 manifest：`tools/tools.yaml` 可选存在。
- 同一来源同名 Skill 启动失败。
- 不同来源同名按 PROJECT > USER > BUILTIN 覆盖。

### okcode.skills.catalog

职责：持有当前有效 Skill 元数据、热更新和完整定义加载。

对外接口：

- SkillCatalog(roots, known_tool_names)。
- refresh()。
- list()。
- get(name)。
- load_definition(name)。

热更新策略：

- `/skill` 执行前调用 refresh。
- LoadSkill 执行前调用 refresh。
- refresh 不修改 SkillActivationStore 里的已激活快照。
- 如果 refresh 发现启动级错误，`/skill` 显示错误，LoadSkill 对目标 Skill 返回 ToolFailure；下次进程启动仍然失败。

### okcode.skills.activation

职责：管理当前会话激活快照、工具可见范围和提示词渲染。

对外接口：

- SkillActivationStore.activate(definition, arguments)。
- SkillActivationStore.clear()。
- SkillActivationStore.render_available_section(catalog)。
- SkillActivationStore.render_active_section()。
- SkillActivationStore.visible_tool_names(default_names, load_skill_name)。
- SkillActivationStore.assert_model_compatible(new_model)。

模型覆盖规则：

- 没有激活 Skill 指定 model 时，不覆盖当前 Provider 模型。
- 已激活 Skill 中最多允许存在一个非空 model。
- 激活第二个不同 model 的 Skill 时，LoadSkill 返回 ToolFailure，保留原激活状态。
- ProviderRequest 新增 `model_override: str | None`，Provider 使用 override 或自身默认 model。

### okcode.skills.tools

职责：实现系统级 LoadSkill 工具和目录型 Skill 专属脚本工具。

对外接口：

- LoadSkillTool(catalog, activation_store, runner)。
- SkillScriptTool(manifest, package_dir)。
- build_skill_tools(definition) -> tuple[Tool, ...]。

LoadSkillTool 定义：

- name: `load_skill`。
- safety: READ_ONLY。
- permission_target: NONE。
- timeout_seconds: 使用固定较长超时，覆盖独立执行场景。
- 不受 Skill 白名单限制，始终进入模型可见工具集合。

LoadSkillTool 行为：

1. refresh catalog。
2. 按 name 查找有效 Skill。
3. load_definition 读取完整 SOP 和专属工具。
4. 校验 arguments 覆盖全部占位符。
5. 构造或替换 SkillActivation。
6. 注册该 Skill 的专属工具。
7. 如果 mode 为 shared，返回“已激活 Skill”工具结果。
8. 如果 mode 为 isolated，调用 SkillRunner，返回隔离执行摘要或失败原因。

### okcode.skills.runner

职责：运行独立模式 Skill 的临时对话。

对外接口：

- SkillRunner.run(activation, history_mode) -> SkillRunResult。

流程：

1. 根据 SkillHistoryMode 选择临时历史。
2. 构造只包含当前 Skill SOP 的 PromptBuildContext。
3. 计算可见工具：该 Skill allowed_tools + dedicated_tools + load_skill。
4. 调用当前 Provider，循环处理工具调用直到最终助手消息或停止原因。
5. 不提交临时 messages 到主 ConversationSession。
6. 成功时返回最终助手内容作为摘要；失败时返回停止原因和可诊断信息。

### okcode.skills.builtin

职责：保存内置 commit、review、test 三个样板 Skill。

文件组织：

- `src/okcode/skills/builtin/commit/SKILL.md`
- `src/okcode/skills/builtin/review/SKILL.md`
- `src/okcode/skills/builtin/test/SKILL.md`

三个内置 Skill 必须走 discovery、catalog、LoadSkill、白名单和激活快照，不在命令 handler 中写特殊逻辑。

### okcode.prompt.builder / sections / runtime

职责调整：

- PromptOptionalSections 新增 `available_skills: str`。
- optional_sections 新增“可用 Skill”区块。
- RuntimePromptContextFactory 接收两个可调用对象：
  - available_skills_provider() -> str。
  - active_skills_provider() -> str。
- PromptBuilder 继续按优先级排序动态系统指令。
- “可用 Skill”只包含 name + description。
- “已激活的 Skill”包含完整 SOP 快照，放在长期记忆之前。

### okcode.conversation.ConversationSession

职责调整：

- 保存 SkillCatalog、SkillActivationStore、SkillRunner 或 SkillRuntime。
- `_build_normal_request` 根据激活状态计算工具可见范围和 active skill 提示。
- `reset_session()` 调用 activation_store.clear()。
- `status_snapshot()` 的可用工具数量改为当前请求可见工具数量或明确保留“注册工具总数”，并在 `/skill` 中展示 Skill 状态，避免混淆。
- `_execute_tool_calls()` 继续使用同一个 ToolExecutor，不绕开权限和结构化结果。
- 新增内部方法 `visible_tools_for(mode, scope)`，集中处理 DEFAULT/PLAN/Skill 白名单。

### okcode.commands.models / handlers / defaults

职责调整：

- CommandContext 新增 `skills: SkillCatalog` 或 `skill_runtime: SkillRuntime`。
- TurnEvent 新增 SkillListEvent。
- handlers 新增 `skill_command`。
- defaults 注册第十三条内置命令 `/skill`，LOCAL 类型。
- `/help`、补全和冲突检查继续由 CommandRegistry 驱动。
- 普通 Skill 不进入 CommandRegistry。

### okcode.terminal

职责调整：

- 渲染 SkillListEvent：显示可加载 Skill 的名称、来源、激活标记和一句说明。
- 如果有解析 issue，显示在列表下方，按 warning/error 区分。
- 状态栏不显示全部 Skill 列表，避免噪音。

### okcode.cli

职责调整：

1. 构建默认工具注册表。
2. 发现 MCP 工具并注册。
3. 注册 LoadSkillTool 之前先创建 SkillCatalog 所需的 roots。
4. 使用 `known_tool_names + load_skill + dedicated_tool_names` 校验 Skill 白名单。
5. 创建 SkillActivationStore 和 SkillRunner。
6. 注册 LoadSkillTool。
7. 构造 RuntimePromptContextFactory 时传入可用 Skill 和激活 Skill 渲染器。
8. 构造 CommandRegistry 时包含 `/skill`。
9. 构造 ConversationSession 时注入 SkillRuntime。

## 模块交互

### 启动流程

```text
cli.main
  → build_default_registry(workspace)
  → discover MCP tools and register
  → SkillRoots.for_workspace(workspace.root)
  → SkillCatalog.discover + validate whitelist
  → SkillActivationStore()
  → SkillRunner(provider, executor, prompt_builder, catalog, activation_store)
  → register LoadSkillTool
  → build command registry including /skill
  → RuntimePromptContextFactory(... skill renderers ...)
  → ConversationSession(... skill_runtime ...)
  → OkCodeApp(...)
```

### 普通对话中使用 Skill

```text
用户：用 review skill 看当前改动
  → 普通文本进入 ConversationSession.stream_user_message
  → Prompt 包含可用 Skill: review - ...
  → 模型调用 load_skill(name="review", arguments={...})
  → ToolExecutor 执行 LoadSkillTool
  → SkillCatalog.refresh + load_definition("review")
  → SkillActivationStore.activate(...)
  → 工具结果写回 pending tool message
  → 下一次 ProviderRequest 包含已激活 Skill 完整 SOP
  → 模型按 SOP 继续执行
  → 成功后 pending 原子提交到主历史
```

### 独立模式 Skill

```text
模型调用 load_skill(name="some_isolated_skill")
  → LoadSkillTool 激活快照
  → SkillRunner 根据 history_mode 选择临时历史
  → SkillRunner 用隔离 messages 调 Provider
  → SkillRunner 执行隔离工具调用
  → SkillRunner 返回摘要
  → LoadSkillTool 把摘要作为工具结果
  → 主 Agent Loop 只看到 load_skill 工具结果
  → 最终助手回答进入主历史
```

### 热更新

```text
用户执行 /skill 或模型调用 load_skill
  → SkillCatalog.refresh()
  → 重新扫描三层目录
  → 更新可加载 Skill 列表和 issue
  → 不修改 activation_store 中已有快照
  → 再次 LoadSkill 同名 Skill 时替换旧快照
```

### 清空对话

```text
用户执行 /clear
  → CommandHandler 返回 RESET_SESSION
  → ConversationSession.reset_session()
  → 清空 messages / saved_plan / token / turn_count
  → activation_store.clear()
  → 新 journal 开始写入
  → 后续请求只显示可用 Skill 元数据
```

## 文件组织

```text
src/okcode/
├── skills/
│   ├── __init__.py
│   ├── models.py
│   ├── frontmatter.py
│   ├── discovery.py
│   ├── catalog.py
│   ├── activation.py
│   ├── tools.py
│   ├── runner.py
│   └── builtin/
│       ├── commit/
│       │   └── SKILL.md
│       ├── review/
│       │   └── SKILL.md
│       └── test/
│           └── SKILL.md
├── commands/
│   ├── models.py
│   ├── handlers.py
│   └── defaults.py
├── prompt/
│   ├── builder.py
│   ├── runtime.py
│   └── sections.py
├── tools/
│   └── registry.py
├── conversation.py
├── terminal.py
├── models.py
└── cli.py

tests/unit/
├── test_skills_frontmatter.py
├── test_skills_discovery.py
├── test_skills_catalog.py
├── test_skills_activation.py
├── test_skills_load_tool.py
├── test_skills_runner.py
├── test_commands_handlers.py
├── test_prompt_builder.py
├── test_prompt_runtime.py
├── test_conversation.py
├── test_app.py
└── test_cli.py
```

目录型 Skill 示例：

```text
.okcode/skills/my_skill/
├── SKILL.md
└── tools/
    ├── tools.yaml
    ├── schemas/
    │   └── inspect_target.json
    └── scripts/
        └── inspect_target.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Skill 根目录 | 内置 `src/okcode/skills/builtin`，用户 `~/.okcode/skills`，项目 `.okcode/skills` | 符合现有工作区本地配置风格，Windows 下路径清晰 |
| 占位符语法 | `{{name}}` | Markdown 中直观，不和 shell、Python f-string、YAML 常见语法强绑定 |
| 多 Skill 工具合并 | 所有已激活 Skill 白名单并集 + 专属工具 + load_skill | 支持多个 Skill 协作，同时不会回退到全量工具 |
| LoadSkill 工具名 | `load_skill` | 与现有 snake_case 工具命名一致，模型可读 |
| `/skill` 行为 | 只列可加载和已激活 Skill | 满足用户确认，不让普通 Skill 占用斜杠命令空间 |
| 热更新策略 | `/skill` 和 LoadSkill 前显式 refresh；激活快照不自动变化 | 无后台 watcher，行为可预测，复现简单 |
| 专属工具暴露名 | `skill__{skill_name}__{local_name}` | 避免目录型 Skill 工具与全局工具或其他 Skill 冲突 |
| 独立模式回流 | LoadSkill 工具结果返回摘要 | 复用现有工具消息历史原子性，不新增特殊历史格式 |
| 模型覆盖 | ProviderRequest 增加 model_override，多个激活 Skill 不允许不同 model | 满足 frontmatter 字段，同时避免多 Skill 冲突 |
| 解析失败 | 单个 Skill issue 化；全局白名单和同源重名启动失败 | 对齐 spec 的失败隔离和启动期强校验 |
| 专属脚本协议 | stdin JSON，stdout ToolOutput JSON | 与 ToolExecutor 结构化结果对齐，测试容易 |
| 启动期正文处理 | 只扫描正文是否存在，不保留完整 SOP | 满足正文非空校验和低上下文成本 |

## spec 覆盖自检

| spec 项 | plan 覆盖位置 |
|---------|---------------|
| F1/F2 | SkillFrontmatter、frontmatter 模块、占位符规则 |
| F3 | 目录型 Skill 文件组织、SkillToolManifest、SkillScriptTool |
| F4 | SkillSourceKind、SkillRoots、discovery 覆盖规则 |
| F5 | SkillParseIssue、discover_skills 错误隔离 |
| F6 | validate_skill_catalog、CLI 启动流程 |
| F7 | LoadSkillTool、启动期提示词、普通对话交互 |
| F8 | SkillActivationStore、prompt builder/runtime 调整 |
| F9 | SHARED 模式、ConversationSession 集成 |
| F10/F11 | ISOLATED 模式、SkillRunner、HistoryMode |
| F12 | visible_tool_names、工具合并技术决策 |
| F13 | `/skill` handler、CommandContext、Terminal 渲染 |
| F14 | SkillCatalog.refresh、激活快照策略 |
| F15 | ConversationSession.reset_session 清 activation_store |
| F16 | builtin Skill 文件和统一 LoadSkill 机制 |

