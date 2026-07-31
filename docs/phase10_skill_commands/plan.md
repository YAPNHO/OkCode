# OkCode 第十阶段补充：Skill 斜杠命令 Plan

## 架构概览

在现有 `SkillCatalog -> SkillRuntime -> CommandRegistry -> CommandDispatcher/TerminalUI` 链路中增加动态命令装配层。`SkillRuntime` 同时管理有效 Skill 快照和与其对应的动态命令快照；它是启动期、`/skill` 刷新期和 `load_skill` 刷新期的唯一同步入口。

命令注册表保持同一个对象实例，通过原子替换内部命令快照更新。`CommandDispatcher` 和 `SlashCommandCompleter` 已持有该对象，因此一次替换会同时影响分发、`/help` 与终端补全，无需重建应用或输入会话。

## 核心数据结构与接口

### CommandRegistry

- `definitions() -> tuple[CommandDefinition, ...]`：返回当前完整命令定义快照，供保留内置命令和测试读取。
- `replace(commands: Iterable[CommandDefinition]) -> None`：先在局部校验所有名称和别名，再一次性替换命令元组及索引；校验失败时保留原快照。

现有 `resolve()`、`visible_commands()` 与 `completion_candidates()` 继续只读取当前快照。

### SkillCatalog

- `prepare_refresh() -> SkillDiscoveryResult`：扫描、解析并校验三层来源，返回尚未提交的候选目录快照。
- `commit_refresh(result: SkillDiscoveryResult) -> None`：提交已通过后续命令冲突校验的候选快照。
- `refresh()` 保留为“prepare 后立即 commit”的兼容入口。

该两阶段接口避免热更新发现命令冲突后，目录内容已经更新而命令列表仍是旧版本的半更新状态。

### SkillRuntime

新增构造输入：基础 `CommandRegistry`。初始化时捕获其内置命令定义作为不可覆盖基线。

- `refresh() -> None`：按以下原子流程刷新目录和命令：
  1. 调用 `SkillCatalog.prepare_refresh()` 得到候选有效 Skill；
  2. 将候选有效 Skill 转为动态 `CommandDefinition`；
  3. 使用基础命令解析每个动态名称，检测内置名称或别名冲突；
  4. 将基础命令和动态命令交给 `CommandRegistry.replace()` 校验并替换；
  5. 仅在命令替换成功后调用 `SkillCatalog.commit_refresh()`。
- `build_skill_commands(skills)`：为每个最终生效 Skill 生成一个 `PROMPT` 类型命令；不读取 SOP 正文。
- `command_handler(skill_name)`：将 `/<skill> [任务文本]` 转为一条明确要求使用该 Skill 的 `ForwardedUserMessage`。该消息要求 Agent 通过 `load_skill` 激活指定 Skill，随后沿用 shared/isolated 的现有执行路径。

冲突使用 `SkillValidationError` 表达，错误信息包含冲突键、动态 Skill 名称与来源路径、以及已有内置命令的主名称。`SkillRuntime` 不改变 `SkillActivationStore`，因此刷新无论成功或失败都不会影响激活快照。

## 模块设计

### 动态命令构造

动态命令使用 Skill 的显示名称作为命令名、空别名、`PROMPT` 类型和“使用 Skill：description”的说明。用闭包或绑定参数的处理函数保存目标 Skill 名称，避免循环变量晚绑定。

命令转发文本必须明确包含 Skill 名称及用户追加的任务文本；为空时使用“基于当前工作区和 Skill SOP 执行”的默认任务。它不直接读取 SOP，也不绕过 `load_skill`、工具白名单、权限或会话提交机制。

### 冲突与覆盖

先执行现有 Skill 发现规则：同一来源同名失败，不同来源同名按 `PROJECT > USER > BUILTIN` 覆盖。之后只对有效 Skill 构造动态命令。

动态命令与基础命令名称或别名冲突时，启动期由 CLI 捕获并以配置错误退出。热更新期由 `/skill` 或 `load_skill` 捕获：命令与目录快照均保持旧版本，`/skill` 输出错误；`load_skill` 转为带诊断信息的 `ToolFailure`。

内置 `review` Skill 文件删除后，静态 `/review` 是唯一同名命令。任何用户或项目 `review` Skill 在成为有效版本后均会与它冲突。

### CLI 和刷新入口

CLI 的装配顺序调整为：创建基础命令注册表，创建尚未提交目录的 `SkillCatalog` 与 `SkillRuntime`，调用 `SkillRuntime.refresh()`，然后继续权限、Provider、`load_skill` 和应用装配。这样所有命令冲突都在 Provider 创建前失败。

`/skill` 继续调用 `SkillRuntime.refresh()`；`LoadSkillTool` 不再直接刷新 `SkillCatalog`，改接收刷新回调并调用 `SkillRuntime.refresh()`。两条入口使用同一事务，保证目录、动态命令和提示词可用 Skill 列表一致。

## 模块交互

```text
启动 / /skill / load_skill
          |
          v
  SkillRuntime.refresh()
          |
          +--> SkillCatalog.prepare_refresh()
          |          |
          |          v
          |    有效 Skill（已应用三级覆盖）
          |
          +--> 构造动态命令并检测与基础命令冲突
          |
          +--> CommandRegistry.replace(基础 + 动态)
          |
          +--> SkillCatalog.commit_refresh()

/<skill> [任务]
          |
          v
动态 PROMPT 命令 -> ForwardedUserMessage
          |
          v
现有 Agent Loop -> load_skill -> 完整 SOP -> shared / isolated 执行
```

## 文件组织

```text
src/okcode/
├── commands/
│   ├── registry.py       # 原子命令快照替换与完整定义读取
│   ├── handlers.py       # 动态 Skill 命令处理函数
│   └── defaults.py       # 仅保留静态内置命令
├── skills/
│   ├── catalog.py        # prepare/commit 两阶段目录刷新
│   ├── runtime.py        # 动态命令装配、冲突校验和刷新事务
│   ├── tools.py          # 复用 SkillRuntime 刷新回调
│   └── builtin/review/   # 删除 SKILL.md
└── cli.py                # 启动期命令/Skill 装配顺序

tests/unit/
├── test_commands_registry.py
├── test_commands_handlers.py
├── test_skills_catalog.py
├── test_skills_conversation.py
├── test_skills_load_tool.py
└── test_cli.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 动态命令载体 | 复用可变的 `CommandRegistry` 实例 | 补全器和分发器可立即看到新快照，不需要重启或重建 PromptSession。 |
| 斜杠命令执行方式 | 转发为明确指定 Skill 的 Agent 请求 | 完整 SOP 仍只通过 `load_skill` 加载，复用现有 shared/isolated、白名单和权限边界。 |
| 刷新一致性 | `SkillCatalog` prepare/commit + 命令替换事务 | 冲突或校验失败时保留旧目录和命令快照，避免半更新。 |
| 覆盖规则 | 先覆盖，再为最终有效 Skill 注册 | 保持项目、用户、内置三级优先级，不让低优先级版本占用命令名。 |
| `/review` 冲突 | 删除内置 review Skill，保留静态 `/review` | 满足用户要求，并将外部同名 Skill 统一纳入启动期冲突诊断。 |
