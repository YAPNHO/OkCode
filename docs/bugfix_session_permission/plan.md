# 会话级权限授权修复 Plan

## 架构概览

本次修复保持 `ToolExecutor -> PermissionManager -> Tool.execute` 的主链路不变，只调整权限请求的授权域、会话授权数据结构和决策顺序。CLI 仍只创建一个父会话 `PermissionManager`；子 Agent、Worktree 和 Team Worker 继续使用独立的权限管理器，不继承父会话临时授权。

会话授权从“工具名 + 完整命令或路径”改为“授权域 + 工具名 + 权限目标类型”。普通 Agent 工具调用与 Hook shell 命令属于不同授权域，避免它们虽然都表现为 `run_command`，却意外共享一条宽范围会话授权。

```text
已校验 ToolCall / Hook shell command
  -> 构造 PermissionRequest
       - origin: tool | hook
       - tool_name
       - target_kind: none | command | path
       - target/display_target
  -> 工作区路径规范化与沙箱检查
  -> 危险命令黑名单
  -> 持久化 YAML 规则解析
       - 选中的 deny：终局拒绝
       - 选中的 allow：直接允许
  -> 会话授权匹配
       - origin + tool_name + target_kind 完全相同：直接允许
  -> strict / default / allow 模式兜底
       - default：请求用户确认
```

## 核心数据结构

### `PermissionRequestOrigin`

新增内部权限请求来源枚举：

```python
class PermissionRequestOrigin(StrEnum):
    TOOL = "tool"
    HOOK = "hook"
```

`PermissionRequest` 增加带默认值的 `origin` 字段，默认是 `TOOL`，保证现有调用方和测试构造方式兼容。`authorize_hook_command_async()` 明确构造 `HOOK` 来源请求。

### `SessionPermissionGrant`

新增不可变、可哈希的内存授权结构：

```python
@dataclass(frozen=True, slots=True)
class SessionPermissionGrant:
    origin: PermissionRequestOrigin
    tool_name: str
    target_kind: PermissionTargetKind
```

匹配条件为三个字段完全相同。授权结构不保存命令文本、路径、参数、工作区绝对路径或持久化规则动作；工作区边界由所属 `PermissionManager` 及其 `Workspace` 自然限定。

`PermissionManager` 使用 `set[SessionPermissionGrant]` 保存 `_session_grants`，自动去重。重新创建权限管理器时集合为空，因此会话授权不会跨进程、跨工作区或跨子 Agent 复制。

### 持久化 `PermissionRule`

现有 `PermissionRule`、`RuleSet` 和 YAML 格式保持不变：

- “永久允许”继续生成包含完整目标的精确 `allow` 规则。
- YAML 内 `pattern=None` 的既有工具级规则继续按原语义工作。
- 不把宽范围会话授权序列化为 `PermissionRule`，避免内存授权和磁盘配置语义混淆。

## 模块设计

### `src/okcode/permissions/models.py`

**职责：** 定义权限请求来源和会话授权值对象。

**改动：**

- 新增 `PermissionRequestOrigin`。
- 在 `PermissionRequest` 末尾新增默认值为 `TOOL` 的 `origin` 字段。
- 新增 `SessionPermissionGrant`，并提供从 `PermissionRequest` 构造授权键的类方法或纯函数。
- 不修改 `PermissionRule.matches()`、规则文本解析和 YAML 序列化契约。

### `src/okcode/permissions/manager.py`

**职责：** 保存会话授权并按安全顺序给出最终权限决定。

**改动：**

- 将 `_session_rules` 替换为 `_session_grants: set[SessionPermissionGrant]`。
- `allow_for_session(request)` 只添加由请求的 `origin`、工具名和目标类型组成的授权键。
- `_build_request()` 接收来源参数；普通 `authorize()` / `authorize_async()` 使用 `TOOL`，Hook 入口使用 `HOOK`。
- 把当前 `_resolve_rule()` 拆为“持久化规则解析”和“会话授权匹配”两个步骤。
- `_authorize_without_confirmation()` 保持沙箱与黑名单最先执行；随后先处理持久化规则已选中的 `deny`/`allow`，再检查会话授权，最后才使用权限模式。
- `_decision_from_confirmation()` 保持四个分支：`ONCE` 不保存状态；`SESSION` 写入 `_session_grants`；`PERMANENT` 继续使用 `_allow_rule_for(request)` 生成精确规则；`DENY` 拒绝。
- `authorize_hook_command_async(background=True)` 仍禁止未获得明确规则或既有 Hook 会话授权的后台确认；普通工具域授权不能让后台 Hook 自动执行。
- 继续返回 `RuleSource.SESSION` 标识会话授权命中，不改变拒绝结果结构。

### `src/okcode/terminal.py`

**职责：** 让用户知道会话选择会放行当前授权域内的同一工具，而不是仅放行当前完整目标。

**改动：**

- 将确认提示中的 `s=本会话` 改为 `s=本会话内允许此工具`。
- 输入按键及 `PermissionConfirmation.SESSION` 映射不变。
- 不修改底部权限模式、`/permission` 命令或其他终端布局。

### `src/okcode/agents/runtime.py`

**职责：** 保持子 Agent 权限状态隔离。

**改动：**

- 继续只复制工作区、持久化规则、工具名和权限模式，不复制 `_session_grants`。
- 若类型或访问方式需要同步，做最小适配；不改变子 Agent 权限模式和非交互确认策略。

## 测试设计

### `tests/unit/test_permissions_manager.py`

- 命令范围复用：首次 `git status` 选择 `SESSION`，随后 `git diff` 命中 `RuleSource.SESSION`，确认回调总计一次。
- 路径范围复用：首次 `write_file(src/a.py)` 选择 `SESSION`，随后 `write_file(src/b.py)` 无需确认。
- 沙箱优先：已有路径工具会话授权时，工作区外路径仍返回 `RuleSource.SANDBOX`，确认回调不增加。
- 工具隔离：`run_command` 的授权不覆盖 `write_file`。
- 授权域隔离：普通 `run_command` 会话授权不覆盖 Hook 命令；Hook 自己获得会话授权后可复用其他 Hook 命令。
- 后台 Hook：只有既有 Hook 域会话授权或持久化规则可以放行；普通工具域授权不能绕过后台无交互拒绝。
- 配置拒绝优先：先建立会话授权，再调用命中项目本地 `deny` 的目标，仍按该规则来源拒绝。
- 生命周期：新建 `PermissionManager` 后相同调用重新确认，且三个 YAML 路径都没有新增会话授权。
- 确认范围：`ONCE` 后下次仍确认；`PERMANENT` 只放行精确目标，另一个目标仍按既有规则处理。

### `tests/unit/test_tools_executor.py`

- 在执行器真实预检入口连续执行两个不同命令，断言确认次数为 1、工具执行次数为 2。
- 保留并复跑拒绝发生在副作用之前、异步确认等待和结构化失败结果测试。

### `tests/unit/test_terminal.py`

- 保留 `s -> PermissionConfirmation.SESSION` 映射测试。
- 增加可见提示断言，确认终端明确显示“本会话内允许此工具”。

### `tests/unit/test_agents_runner.py`

- 将当前依赖私有 `_session_rules` 的断言改成行为断言或新的 `_session_grants` 空集合断言。
- 验证子 Agent 创建的会话授权不修改父管理器，父会话授权也不自动复制给新子管理器。

## 模块交互

1. `ToolExecutor.prepare()` 调用 `PermissionManager.authorize_async()`，请求来源默认为普通工具域。
2. `HookActionRunner` 通过 `authorize_hook_command_async()` 进入 Hook 域。
3. `PermissionManager` 在构造路径请求时先使用绑定的 `Workspace` 完成规范化和越界拒绝。
4. 黑名单和持久化规则给出结论后立即返回；只有它们没有结论时才查询 `_session_grants`。
5. `default` 模式下用户选择 `SESSION` 后，当前调用被允许，并把授权键写入集合。
6. 后续请求仅在来源、工具名和目标类型完全相同时命中；命中后不再调用终端确认。
7. 允许结果沿用 `ToolExecutor.execute_prepared()`；拒绝结果沿用现有 `permission_source`、`executed=false` 封装。

## 文件组织

```text
docs/bugfix_session_permission/
├── spec.md
├── plan.md
├── task.md
└── checklist.md

src/okcode/permissions/models.py
src/okcode/permissions/manager.py
src/okcode/terminal.py
src/okcode/agents/runtime.py          # 仅在隔离适配需要时修改

tests/unit/test_permissions_manager.py
tests/unit/test_tools_executor.py
tests/unit/test_terminal.py
tests/unit/test_agents_runner.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 会话授权模型 | 独立 `SessionPermissionGrant` | 不与可序列化 `PermissionRule` 混淆，能表达授权域和目标类型 |
| 授权粒度 | 来源域 + 工具名 + 目标类型 | 解决不同命令重复确认，同时避免跨工具、跨 Hook 共享 |
| 工作区范围 | 由 `PermissionManager` 绑定的 `Workspace` 限定 | 无需在授权键保存敏感绝对路径，子 Worktree 自然隔离 |
| 决策顺序 | 沙箱/黑名单 > 持久化规则 > 会话授权 > 模式 | 确保终局安全检查和显式 deny 不可绕过 |
| 永久允许 | 保持精确目标规则 | 不把临时便利扩展为永久工具级授权 |
| Hook 行为 | 独立授权域 | 防止一次普通命令授权静默放行后台 Hook shell |
| 子 Agent 行为 | 不继承、不回写父会话授权 | 保持既有权限隔离和非交互安全边界 |
| UI | 明示“允许此工具” | 让用户理解会话授权的实际扩大范围 |

## Spec 覆盖

| Spec 项 | 设计归属 |
|---|---|
| F1 | `SessionPermissionGrant`、请求来源和目标类型匹配 |
| F2 | `PermissionManager` 决策顺序及安全优先级测试 |
| F3 | 四种确认分支、精确永久规则和内存授权集合 |
| F4 | 管理器实例生命周期、工作区及子 Agent 隔离 |
| F5 | `RuleSource.SESSION` 与执行器现有结构化拒绝封装 |
| N1 | 保持同步/异步授权、执行器和 CLI 接口 |
| N2 | 不新增磁盘格式或配置项 |
| N3 | 不可变授权键和确定性匹配 |
| N4 | 继续复用 `Workspace` 的 Windows 路径规范化 |
| AC1 | 命令工具跨目标复用测试、执行器确认次数测试 |
| AC2 | 路径工具跨目标复用与工作区外沙箱测试 |
| AC3 | 新管理器空授权集合、YAML 无写入测试 |
| AC4 | 已有会话授权下的黑名单终局拒绝测试 |
| AC5 | 已有会话授权下的持久化 `deny` 优先测试 |
| AC6 | `ONCE` 不复用、`PERMANENT` 保持精确目标测试 |
| AC7 | 执行器异步确认、事件顺序、终端与子 Agent 回归测试 |
| AC8 | 定向测试、全量测试、Ruff 与 Git 差分检查 |
