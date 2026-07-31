# OkCode 第十阶段补充：Skill 斜杠命令 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/okcode/commands/registry.py` | 原子替换命令快照与完整定义读取 |
| 修改 | `src/okcode/commands/handlers.py` | 动态 Skill 命令转发处理函数 |
| 修改 | `src/okcode/skills/catalog.py` | 目录刷新 prepare/commit 事务 |
| 修改 | `src/okcode/skills/runtime.py` | 动态命令构造、冲突校验与统一刷新 |
| 修改 | `src/okcode/skills/tools.py` | 通过运行时刷新回调加载 Skill |
| 修改 | `src/okcode/cli.py` | 启动期先装配动态命令再创建 Provider |
| 删除 | `src/okcode/skills/builtin/review/SKILL.md` | 移除与内置 `/review` 冲突的样板 Skill |
| 修改 | `tests/unit/test_commands_registry.py` | 覆盖命令快照原子替换 |
| 修改 | `tests/unit/test_commands_handlers.py` | 覆盖动态命令的转发语义 |
| 修改 | `tests/unit/test_skills_catalog.py` | 覆盖 prepare/commit 与目录覆盖规则 |
| 修改 | `tests/unit/test_skills_load_tool.py` | 覆盖 load 时刷新动态命令及冲突失败 |
| 修改 | `tests/unit/test_skills_conversation.py` | 覆盖 `/commit` 到 `load_skill` 的两阶段流程 |
| 修改 | `tests/unit/test_cli.py` | 覆盖启动期命令装配和冲突失败 |
| 修改 | `tests/unit/test_terminal.py` | 覆盖动态命令补全和 `/help` 可见性 |

## T1：命令注册表支持原子替换

**文件：** `src/okcode/commands/registry.py`、`tests/unit/test_commands_registry.py`  
**依赖：** 无

**步骤：**
1. 提取现有命令索引构造逻辑为局部构造函数，使重复名、空名和别名冲突在替换前全部校验。
2. 增加读取当前完整命令定义的接口，供 SkillRuntime 保存静态内置命令基线。
3. 增加替换接口；仅在新命令定义和索引均构造成功后替换内部字段。
4. 编写测试，证明替换成功后 resolve、visible commands、补全共同更新；替换失败后旧集合不变。

**验证：** `uv run pytest tests/unit/test_commands_registry.py -q`

## T2：将 SkillCatalog 拆为候选构造和提交

**文件：** `src/okcode/skills/catalog.py`、`tests/unit/test_skills_catalog.py`  
**依赖：** 无

**步骤：**
1. 增加不修改当前有效目录的候选刷新接口，复用既有发现和工具白名单校验。
2. 增加显式提交接口，并保留原 `refresh()` 作为兼容封装。
3. 确保列表、定义加载和 issue 展示继续读取最后一次已提交快照。
4. 编写测试，证明候选失败或未提交时旧有效 Skill 保持不变。

**验证：** `uv run pytest tests/unit/test_skills_catalog.py tests/unit/test_skills_discovery.py -q`

## T3：构造并校验动态 Skill 命令

**文件：** `src/okcode/commands/handlers.py`、`src/okcode/skills/runtime.py`、`tests/unit/test_commands_handlers.py`  
**依赖：** T1、T2

**步骤：**
1. 为有效 Skill 构造无别名的 `PROMPT` 命令，显示说明包含 Skill 名称和元数据说明。
2. 实现绑定目标 Skill 名称的命令处理函数，将可选任务文本转为明确要求使用该 Skill 的转发用户消息。
3. 让 SkillRuntime 捕获基础命令基线，检查动态命令与内置名称、内置别名的冲突，并生成包含 Skill 路径和命令所有者的 `SkillValidationError`。
4. 编写测试，验证 `/commit 任务` 的转发内容、空参数行为、`/help` 排序和与 `/review` 的冲突诊断。

**验证：** `uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_commands_registry.py -q`

## T4：实现 Skill 与命令的统一刷新事务

**文件：** `src/okcode/skills/runtime.py`、`tests/unit/test_skills_catalog.py`  
**依赖：** T1、T2、T3

**步骤：**
1. 实现 `SkillRuntime.refresh()` 的候选目录、动态命令、命令替换、目录提交顺序。
2. 保持三级来源覆盖后只注册最终有效版本的一个命令。
3. 确保命令冲突时目录和命令均保持上次成功快照，且不改动已激活 Skill。
4. 编写新增、删除、覆盖、冲突热更新测试，分别断言命令列表和激活快照。

**验证：** `uv run pytest tests/unit/test_skills_catalog.py tests/unit/test_skills_activation.py -q`

## T5：接入 `/skill`、`load_skill` 与 CLI 启动流程

**文件：** `src/okcode/skills/tools.py`、`src/okcode/cli.py`、`tests/unit/test_skills_load_tool.py`、`tests/unit/test_cli.py`  
**依赖：** T4

**步骤：**
1. 将 LoadSkillTool 的目录刷新替换为注入的 SkillRuntime 刷新回调，保留 ToolFailure 诊断转换。
2. 调整 CLI：先创建静态命令注册表、未提交目录和 SkillRuntime，成功刷新动态命令后才继续权限和 Provider 装配。
3. 验证启动期冲突在 Provider 创建前返回可显示配置错误。
4. 验证 `load_skill` 热更新后命令注册表同步，冲突时旧命令与旧目录快照保留。

**验证：** `uv run pytest tests/unit/test_skills_load_tool.py tests/unit/test_cli.py -q`

## T6：删除 review 样板并调整端到端覆盖

**文件：** `src/okcode/skills/builtin/review/SKILL.md`、`tests/unit/test_skills_conversation.py`、`tests/unit/test_terminal.py`  
**依赖：** T5

**步骤：**
1. 删除内置 `review` Skill 文件，保留原有静态 `/review` 处理函数和测试。
2. 将内置 Skill 断言调整为 `commit`、`test`；确认 `/skill` 不再列出 `review`。
3. 添加 `/commit` 两阶段测试：先生成指定 Skill 的 Agent 请求，再由 `load_skill` 注入完整 SOP 并继续执行。
4. 添加补全和 `/help` 测试，确认动态 `/commit`、`/test` 可见，而 `/review` 仍是静态命令。

**验证：** `uv run pytest tests/unit/test_skills_conversation.py tests/unit/test_terminal.py tests/unit/test_commands_handlers.py -q`

## T7：完整回归和验收

**文件：** 全项目  
**依赖：** T1-T6

**步骤：**
1. 运行 Skill、命令、CLI、终端相关单元测试并修复回归。
2. 运行集成测试和全量 pytest，确认 Provider 序列化、工具调用和会话原子提交保持正常。
3. 运行 Ruff 格式与 lint、Git 空白检查；修复所有静态问题。
4. 按 checklist 的端到端场景记录最终证据。

**验证：** `uv run pytest -q`、`uv run ruff format --check .`、`uv run ruff check .`、`git diff --check`

## 执行顺序

```text
T1 ─┐
    ├─> T3 ─> T4 ─> T5 ─> T6 ─> T7
T2 ─┘
```

## 自检

- plan 覆盖：命令注册表、目录 prepare/commit、SkillRuntime、LoadSkill、CLI 和内置样板均有对应任务。
- 依赖链：先确保命令和目录都能事务更新，再接入运行时、CLI 和用户可见流程。
- 范围控制：不新增参数 schema、Skill 分发、版本管理或内置命令覆盖能力。
