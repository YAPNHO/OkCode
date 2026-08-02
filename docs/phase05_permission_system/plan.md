# OkCode 第五阶段：五层权限系统 Plan

## 架构概览

本阶段在现有“会话层负责 Agent Loop、工具执行器负责参数校验和结果封装、具体工具负责业务执行”的分层上新增独立的 `permissions` 模块。权限模块不依赖 Provider 或终端渲染；它接收已校验的工具调用和工作区上下文，生成允许、拒绝或需要确认的决定。

权限检查位于参数 JSON 解析和 JSON Schema 校验之后、实际工具执行之前。这样规则可匹配可靠的结构化参数，非法调用仍沿用现有 `invalid_json` 或 `invalid_arguments` 结果，且所有有副作用的工具在获得允许前都不会进入原有实现。

```text
模型 ToolCall
  -> ToolExecutor：查询工具、解析 JSON、校验 Schema
  -> PermissionManager：
       1. Windows 危险命令黑名单
       2. 工作区路径规范化与符号链接边界
       3. 会话 / 本地 / 项目 / 用户规则
       4. strict / default / allow 模式
       5. default 模式的人在回路确认
  -> 允许：原有 Tool.execute(arguments)
  -> 拒绝：结构化 ToolExecutionResult
  -> ConversationSession：将结果回灌模型，继续 Agent Loop
```

黑名单、路径沙箱和命中 `deny` 是终局拒绝。模式只处理没有规则命中的调用；`default` 模式才进入人工确认。为了避免并发只读工具同时竞争终端输入，会话层会先按原始工具调用顺序逐一完成权限预检，再把已被允许的只读调用并发执行。预检拒绝的调用直接产生工具结果，不会启动工具实现。

## 核心数据结构

### `PermissionMode`、`RuleAction` 与 `RuleSource`

```python
class PermissionMode(StrEnum):
    STRICT = "strict"
    DEFAULT = "default"
    ALLOW = "allow"


class RuleAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class RuleSource(StrEnum):
    BLACKLIST = "blacklist"
    SANDBOX = "sandbox"
    SESSION = "session"
    PROJECT_LOCAL = "project_local"
    PROJECT = "project"
    USER = "user"
    MODE = "mode"
    USER_CONFIRMATION = "user_confirmation"
```

- `PermissionMode` 表示规则未命中时的总体行为，缺省为 `default`。
- `RuleAction` 仅允许 `allow` 和 `deny`，配置加载时严格校验。
- `RuleSource` 是可观测的决定来源；它写入工具结果数据，供终端展示与测试断言。

### `PermissionTarget`、`PermissionRequest` 与工具元信息

```python
class PermissionTargetKind(StrEnum):
    NONE = "none"
    COMMAND = "command"
    PATH = "path"


@dataclass(frozen=True, slots=True)
class PermissionTarget:
    kind: PermissionTargetKind
    argument_name: str | None = None
    optional: bool = False


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    call: ToolCall
    tool: ToolDefinition
    arguments: Mapping[str, JSONValue]
    target: str | None
    display_target: str | None
```

`ToolDefinition` 增加权限目标描述，使每个已注册工具显式声明其主操作参数：

| 工具 | 权限目标 | 规则匹配值 |
|---|---|---|
| `read_file`、`write_file`、`edit_file` | `path` | 经工作区规范化后的相对路径 |
| `find_files`、`search_code` | 可选 `path` | 指定目录；未指定时为 `.` |
| `run_command` | `command` | 原始完整命令文本 |

无主目标的新工具可声明 `NONE`，此时规则仅按工具名匹配。权限模块不通过硬编码已知工具名判断参数，从而让后续工具能安全接入。

### `PermissionRule`、`RuleSet` 与 `PermissionDecision`

```python
@dataclass(frozen=True, slots=True)
class PermissionRule:
    tool_name: str
    pattern: str | None
    action: RuleAction


@dataclass(frozen=True, slots=True)
class RuleSet:
    source: RuleSource
    rules: tuple[PermissionRule, ...]


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    source: RuleSource
    reason: str
    requires_confirmation: bool = False
```

规则文本语法为 `工具名` 或 `工具名(模式)`。例如 `Bash(git *)` 匹配命令工具的 `git ` 前缀，`write_file(src/**/*.py)` 匹配指定路径。`Bash` 是兼容别名，加载时规范化为实际工具名 `run_command`；`run_command(git *)` 与其等价。其他工具名必须与注册表中的实际名称一致。

模式使用 `fnmatch` 语义：不包含 glob 元字符时即为精确匹配，包含 `*`、`?` 或字符类时按 glob 匹配。Windows 路径在规范化后以正斜杠、大小写无关的方式匹配；命令文本保留原样进行匹配，避免修改 PowerShell 或 CMD 参数含义。

## 规则文件与配置

三个 YAML 来源使用相同、严格校验的格式：

```yaml
rules:
  - match: "Bash(git *)"
    action: allow
  - match: "write_file(.env)"
    action: deny
```

规则文件位置与优先级从高到低如下：

| 来源 | 路径 | 用途 |
|---|---|---|
| 会话 | 仅内存 | “本会话允许”生成，进程退出即丢失 |
| 项目本地 | `<项目根目录>/.okcode/permissions.local.yaml` | “永久允许”写入，默认不提交 |
| 项目级 | `<项目根目录>/.okcode/permissions.yaml` | 团队可共享的项目规则 |
| 用户全局 | `%USERPROFILE%/.okcode/permissions.yaml` | 用户的跨项目默认规则 |

规则匹配按来源优先级逐层进行。每层按 YAML 声明顺序检查，首条匹配规则给出本层结论；该层没有命中才继续下一层。缺少文件等价于空规则集。存在但语法错误、字段未知、`action` 非法、规则文本为空、括号不完整、工具名未知或 `match` 类型错误时，加载阶段抛出带文件路径和规则索引的配置错误。

`.gitignore` 会从“忽略整个 `.okcode/`”改为“忽略其中所有文件，但重新包含 `.okcode/permissions.yaml`”；`permissions.local.yaml` 保持忽略。因此共享项目规则可被 Git 跟踪，用户确认生成的本地规则不会被意外提交。

整体权限模式不写入规则文件，默认值为 `default`。会话支持以下终端命令：

```text
/permissions                 # 显示默认值、当前生效模式和三层规则文件路径
/permissions strict          # 本会话切换为严格模式
/permissions default         # 本会话切换为默认确认模式
/permissions allow           # 本会话切换为放行模式
```

这项切换只存在于当前 OkCode 进程，重启后回到默认模式。终端欢迎信息和 `/permissions` 输出都会以简体中文显示当前模式与规则路径，避免用户误判生效范围。

## 模块设计

### `src/okcode/permissions/models.py`

职责：定义权限模式、规则动作、规则来源、目标描述、规则、请求、决定和确认选择等领域结构。

对外接口：

- `parse_rule_text(text, known_tool_names)`：解析 `工具名(模式)` 并规范化 `Bash` 别名。
- `PermissionRule.matches(request)`：判断工具名与目标模式是否命中。
- `PermissionDecision.to_failure(call)`：将拒绝稳定转换为带来源、类别与可行建议的 `ToolExecutionResult`。

### `src/okcode/permissions/blacklist.py`

职责：维护不可配置放开的 Windows 命令黑名单。

在 `run_command` 的完整命令文本上使用预编译、大小写无关的正则。规则覆盖以下高破坏类别：递归删除盘符根目录或系统目录、格式化卷、磁盘或分区清空/删除、启动配置破坏、强制关机或重启。命中只返回类别化中文原因，例如“命令属于磁盘或系统破坏类操作”，不把完整正则或绕过细节暴露给模型。任何 `allow` 规则、`allow` 模式和人工允许均不会覆盖该决定。

### `src/okcode/permissions/rules.py`

职责：安全加载三层 YAML、验证规则、创建规则集、匹配规则及更新本地永久规则。

对外接口：

- `load_permission_rules(workspace_root, known_tool_names)`：按用户、项目、项目本地顺序加载三层规则。
- `RuleResolver.resolve(request)`：按会话、本地、项目、用户顺序返回首个命中决定。
- `append_local_allow_rule(request)`：以保留已有规则顺序的方式写入项目本地 YAML；创建 `.okcode` 目录与文件时使用 UTF-8、原子替换写入。

永久允许仅在用户明确选择后调用写入函数。若写入失败，当前调用不执行，向用户返回可理解的权限拒绝，避免出现“以为永久允许但规则未保存”的状态。

### `src/okcode/permissions/manager.py`

职责：按五层顺序编排完整决策，并保存当前会话的临时规则与模式。

对外接口：

- `authorize(request)`：依次执行黑名单、路径沙箱、规则解析、模式兜底和可选人工确认，返回终局决定。
- `set_mode(mode)` 与 `status()`：更新和报告当前会话模式。
- `allow_for_session(request)`：添加内存 `allow` 规则。

路径目标由 `Workspace` 的解析能力得到。对现有文件、符号链接和 Windows 目录联接，先解析到规范路径再验证位于工作区根目录内；写入不存在的目标时以非严格解析解析所有已存在祖先，避免经父目录符号链接逃逸。规范化结果只以项目相对路径进入规则匹配和用户展示，绝不将外部绝对路径回灌模型。

`default` 模式需要确认时，管理器调用注入的同步确认回调。该回调只在会话层顺序预检阶段运行，避免工具并发时多个调用同时读终端。确认结果为拒绝、本次允许、本会话允许或永久允许；EOF、空输入、异常和未知选择均映射为拒绝。

### `src/okcode/tools/models.py` 与 `src/okcode/tools/defaults.py`

职责：为工具声明权限目标，装配时让权限层能够识别所有已注册工具。

主要改动：

- `ToolDefinition` 新增 `permission_target`，默认 `NONE`，避免未来新增工具被隐式视为安全。
- 六项默认工具分别声明路径、命令或无目标属性。
- `build_default_registry` 保持工具集合与原先一致，不在注册表中混入权限策略。

### `src/okcode/tools/workspace.py`

职责：成为文件工具与权限模块共用的单一 Windows 路径规范化实现。

主要改动：

- 抽取“原始相对路径 -> 解析重解析点后的规范工作区路径 -> 项目相对显示路径”的公共入口。
- 继续拒绝空路径、父级回退、项目外绝对路径、UNC 路径与盘符根目录。
- 对路径检查保持现有 `ToolFailure` 防线，即使将来某段调用未经过权限模块，文件工具仍不会越出工作区。

### `src/okcode/tools/executor.py`

职责：保持统一执行入口，并在参数校验后接入权限预检。

主要改动：

- 构造时可注入 `PermissionManager`；未注入时只用于已有独立工具测试，不改变其原有行为。正式 CLI 装配必须注入权限管理器。
- 新增“解析并校验后生成权限请求”“预检调用”“按既有预检决定执行”的内部边界。
- 无法允许的调用产生 `permission_denied` 或保留 `outside_workspace` 的结构化结果，数据包含 `permission_source`、稳定的拒绝类别和可行动提示。
- 已通过预检的调用复用原有超时、异常转换、输出截断和 `ToolOutput` 封装逻辑。

### `src/okcode/conversation.py`

职责：在不改变 Agent Loop 原子提交语义的前提下，顺序完成权限预检并将拒绝结果继续回灌模型。

主要改动：

- 将每个工具批次拆为“按原顺序预检”与“仅对已允许调用执行”两步。
- 预检拒绝直接产生 `ToolExecutionFinished`，不调用工具实现；允许的只读工具仍可并发，副作用工具仍串行。
- 工具结果仍按模型请求原始顺序加入 `ChatMessage(role=TOOL)`，所以模型能在下一迭代看到权限失败并调整策略。
- 识别 `/permissions` 和 `/permissions <mode>`，不调用 Provider，不写普通对话历史；通过新事件通知终端显示状态。

### `src/okcode/models.py`、`src/okcode/terminal.py` 与 `src/okcode/app.py`

职责：承载权限状态和人工确认的用户界面，不让 Provider 层参与交互。

主要改动：

- 新增权限模式状态事件，供 `/permissions` 命令显示。
- `TerminalUI` 新增明确、单次阻塞的权限确认方法：展示工具名、项目相对目标、当前模式和四种选择；终端中断或无效输入默认拒绝。
- CLI 在构造 `Workspace` 后加载规则、创建 `PermissionManager`，把终端确认回调注入管理器，再把管理器交给 `ToolExecutor` 和会话。权限 YAML 错误使用现有配置错误展示路径。
- 欢迎信息补充当前权限模式；工具完成摘要对权限拒绝显示其决定来源。

### `src/okcode/config.py` 与 `.gitignore`

职责：保持 Provider 配置契约不变，并安排项目规则文件的 Git 追踪边界。

主要改动：

- Provider 的 `config.yaml` 格式不增加权限字段，避免把机器相关权限状态和 API 配置耦合。
- `.gitignore` 重新包含 `.okcode/permissions.yaml`，仍忽略 `.okcode/permissions.local.yaml` 与其他运行时 `.okcode` 内容。

## 模块交互

### 命中规则的调用

```text
模型调用 write_file(src/app.py)
  -> Executor 校验参数
  -> Workspace 解析 src/app.py 并检查项目边界
  -> 会话规则未命中
  -> 本地规则命中 allow
  -> 执行 write_file
  -> 成功结果回灌模型
```

### 默认模式下的确认与永久允许

```text
模型调用 run_command(git status)
  -> 黑名单未命中
  -> 所有规则未命中，模式为 default
  -> 终端显示命令并等待选择
  -> 用户选择永久允许
  -> 写入 .okcode/permissions.local.yaml 的 Bash(git status) allow 规则
  -> 当前调用执行
  -> 后续相同调用由项目本地规则直接允许
```

### 拒绝后继续 Agent Loop

```text
模型调用 run_command(危险命令)
  -> 黑名单终局拒绝，不启动 shell
  -> permission_denied 结构化工具结果写入本轮临时上下文
  -> 下一次 Provider 调用看到拒绝原因与可行建议
  -> 模型改用安全工具调用或给出正式回答
  -> 仅最终回答成功时提交完整本轮历史
```

## 文件组织

```text
src/okcode/
├── cli.py                           # 装配 Workspace、权限管理器、Executor 与会话
├── conversation.py                  # 权限预检调度、/permissions 命令、Agent Loop 回灌
├── models.py                        # 权限模式状态事件
├── terminal.py                      # 权限确认、状态和拒绝来源渲染
├── tools/
│   ├── executor.py                  # 参数校验后的统一权限入口
│   ├── models.py                    # ToolDefinition 权限目标声明
│   ├── workspace.py                 # Windows 规范路径与沙箱检查
│   └── defaults.py                  # 默认工具权限目标装配
└── permissions/
    ├── __init__.py                  # 权限模块公开入口
    ├── models.py                    # 权限领域结构和规则匹配
    ├── blacklist.py                 # Windows 高危命令黑名单
    ├── rules.py                     # YAML 加载、优先级解析和本地持久化
    └── manager.py                   # 五层决策与会话状态

tests/
├── unit/test_permissions_blacklist.py   # 不可放开的 Windows 高危命令
├── unit/test_permissions_rules.py       # YAML、glob、别名、四层优先级与持久化
├── unit/test_permissions_manager.py     # 沙箱、模式与确认范围
├── unit/test_tools_executor.py          # 统一入口在执行前拒绝
├── unit/test_tools_workspace.py         # Windows 风格路径和符号链接逃逸
├── unit/test_conversation.py            # 拒绝回灌、预检顺序和 /permissions
└── unit/test_terminal.py                # 确认输入与权限状态渲染

.gitignore                             # 共享规则可提交，本地规则仍忽略
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 权限检查位置 | `ToolExecutor` 参数校验后、工具执行前 | 同时保护全部本地工具，且规则匹配的是可靠参数 |
| 硬防御顺序 | 黑名单 -> 路径沙箱 -> 规则 -> 模式 -> 确认 | 前置终局拒绝不可被后续信任层绕过 |
| 规则表达 | YAML 列表中的 `match` + `action` | 保留规则顺序，便于首个命中和严格报错定位 |
| 命令工具别名 | `Bash` 规范化为 `run_command` | 兼容用户给出的规则格式，同时保留当前内部工具名 |
| 路径匹配 | 解析符号链接后转项目相对 POSIX 形式 | 防逃逸且使 Windows 分隔符规则稳定、可移植 |
| 命令匹配 | 原始完整命令字符串 | 不改写 PowerShell/CMD 参数，避免规则判断改变命令含义 |
| 人工确认位置 | 会话预检阶段的同步终端回调 | 保障终端交互串行，避免并发工具竞争输入 |
| 永久允许位置 | `.okcode/permissions.local.yaml` | 仅影响当前工作副本，默认不进入版本控制 |
| 模式切换 | `/permissions` 会话命令，默认 `default` | 用户可见、立即生效，不把本机信任状态写进项目配置 |
| 失败表达 | 结构化工具结果而非异常 | 模型可调整策略，Agent Loop 保持继续执行语义 |
| 命令沙箱边界 | 文件工具强制工作区沙箱；命令工具仍依赖黑名单、规则、模式与确认 | 当前 shell 可执行任意脚本，未做操作系统级隔离；不能将字符串检查误称为完整命令沙箱 |

## Spec 覆盖

| Spec 项 | 设计归属 |
|---|---|
| F1 | `ToolExecutor` 统一权限入口与 `PermissionManager` |
| F2 | `permissions/blacklist.py` |
| F3、F10 | `Workspace` 规范路径接口与路径权限目标 |
| F4 | `PermissionRule`、`ToolDefinition.permission_target`、glob 匹配 |
| F5 | `rules.py` 三层 YAML 与 `RuleResolver` |
| F6 | `PermissionManager` 内存会话规则 |
| F7 | `PermissionMode` 与 `/permissions` 命令 |
| F8 | `TerminalUI` 确认回调与 `append_local_allow_rule` |
| F9 | `ToolExecutionResult` 权限失败 + `ConversationSession` 回灌 |
| N1 | 固定五层顺序和终局拒绝 |
| N2 | 保持工具实现、调度、Provider 与 Plan Mode 边界 |
| N3 | `RuleSource`、稳定规则顺序与结果数据 |
| N4 | 安全 YAML、严格校验和 `.gitignore` 边界 |
| N5 | 会话层顺序预检和终端单次确认 |
| N6 | 独立权限单元测试与可控 Provider Agent Loop 测试 |
