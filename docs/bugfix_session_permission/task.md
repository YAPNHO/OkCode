# 会话级权限授权修复 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/okcode/permissions/models.py` | 定义权限请求来源和会话授权键 |
| 修改 | `src/okcode/permissions/manager.py` | 保存会话授权并调整权限决策顺序 |
| 修改 | `src/okcode/terminal.py` | 明确展示会话授权的工具级范围 |
| 修改 | `tests/unit/test_permissions_manager.py` | 覆盖授权范围、安全优先级、Hook 域和生命周期 |
| 修改 | `tests/unit/test_tools_executor.py` | 覆盖执行器中的跨命令会话复用 |
| 修改 | `tests/unit/test_terminal.py` | 覆盖会话授权提示文本和按键映射 |
| 修改 | `tests/unit/test_agents_runner.py` | 覆盖父子 Agent 会话授权隔离 |

`src/okcode/agents/runtime.py` 预计无需修改：它当前通过构造新的 `PermissionManager` 实现隔离，只需用测试确认新会话授权集合不会被复制。如果实现时发现类型访问必须同步，只允许做与该隔离直接相关的最小改动。

## T1：增加权限请求来源与会话授权键

**文件：** `src/okcode/permissions/models.py`

**依赖：** 无

**步骤：**

1. 新增 `PermissionRequestOrigin`，包含 `TOOL` 和 `HOOK`。
2. 在 `PermissionRequest` 最后增加默认值为 `PermissionRequestOrigin.TOOL` 的 `origin` 字段，保持现有位置参数构造兼容。
3. 新增不可变 `SessionPermissionGrant`，字段为 `origin`、`tool_name` 和 `target_kind`。
4. 为 `SessionPermissionGrant` 提供 `from_request(request)`，只读取授权域、调用工具名和权限目标类型，不保存完整命令、路径或参数。
5. 检查新类型没有进入 YAML 解析或序列化路径。

**验证：** 运行 `uv run python -c "from okcode.permissions.models import PermissionRequestOrigin, SessionPermissionGrant; print(PermissionRequestOrigin.TOOL.value)"`，期望输出 `tool`；运行 `uv run ruff check src/okcode/permissions/models.py`，期望通过。

## T2：接入授权域并替换会话状态

**文件：** `src/okcode/permissions/manager.py`

**依赖：** T1

**步骤：**

1. 导入 `PermissionRequestOrigin` 和 `SessionPermissionGrant`。
2. 将 `_session_rules` 替换为 `_session_grants: set[SessionPermissionGrant]`。
3. 修改 `allow_for_session(request)`，使用 `SessionPermissionGrant.from_request(request)` 加入集合并自动去重。
4. 让 `_build_request()` 接收来源参数，并在构造 `NONE`、`COMMAND`、`PATH` 三种 `PermissionRequest` 时保留该来源。
5. 普通 `authorize()` 和 `authorize_async()` 传入 `TOOL`；`authorize_hook_command_async()` 传入 `HOOK`。
6. 增加会话授权匹配方法，只在授权键完全相同时返回 `RuleSource.SESSION`。

**验证：** 运行 `uv run ruff check src/okcode/permissions/manager.py`；使用短脚本构造一个普通命令请求和一个 Hook 命令请求，期望二者生成的授权键来源分别为 `tool`、`hook`。

## T3：调整权限规则解析顺序

**文件：** `src/okcode/permissions/manager.py`

**依赖：** T2

**步骤：**

1. 将 `_resolve_rule()` 改为只按现有顺序解析项目本地、项目、用户持久化规则，不再混入会话状态。
2. 在 `_authorize_without_confirmation()` 中保留请求构造、路径沙箱和黑名单的现有前置位置。
3. 持久化规则命中时立即按选中的 `ALLOW` 或 `DENY` 返回，确保显式拒绝不被会话授权覆盖。
4. 持久化规则无结论时再检查 `_session_grants`；命中则返回允许且来源为 `SESSION`。
5. 未命中会话授权时继续执行 `STRICT`、`ALLOW`、`DEFAULT` 的现有兜底逻辑。
6. 保持 `PERMANENT` 分支使用 `_allow_rule_for(request)` 创建完整目标精确规则，不复用会话授权键。
7. 检查 `authorize_hook_command_async(background=True)`：普通工具域授权不能放行 Hook；Hook 域已有授权时允许，无授权时继续安全拒绝而不弹交互框。

**验证：** 运行 `uv run pytest tests/unit/test_permissions_manager.py -q`，先确认未更新断言以外没有意外异常；运行 `uv run ruff check src/okcode/permissions/manager.py`。

## T4：补齐权限管理器行为测试

**文件：** `tests/unit/test_permissions_manager.py`

**依赖：** T3

**步骤：**

1. 增加可记录确认次数和顺序的 confirmer 替身。
2. 修改会话复用测试：`git status` 选择 `SESSION` 后，`git diff` 直接命中 `SESSION`，确认总次数为 1。
3. 增加路径工具测试：同一 `write_file` 的两个工作区内路径共享授权，工作区外路径仍由 `SANDBOX` 拒绝且不调用 confirmer。
4. 增加工具隔离测试：`run_command` 的授权不覆盖 `write_file`。
5. 增加授权域隔离测试：普通 `run_command` 授权不覆盖 Hook；Hook 自身选择 `SESSION` 后可复用不同 Hook 命令。
6. 增加后台 Hook 测试：普通工具域授权不能放行后台 Hook，既有 Hook 域授权可以放行。
7. 增加持久化 `deny` 优先测试：已有会话授权后，命中项目本地 `deny` 的目标仍拒绝并保留来源。
8. 增加生命周期测试：重建管理器后授权集合为空，三个 YAML 文件均未因 `SESSION` 创建或修改。
9. 增加 `ONCE` 与 `PERMANENT` 边界断言：前者不复用，后者只对精确目标持久化。
10. 保留黑名单、三档模式、`/exit` 和永久写入失败等既有覆盖。

**验证：** 运行 `uv run pytest tests/unit/test_permissions_manager.py -q`，期望全部通过。

## T5：增加执行器级会话复用回归测试

**文件：** `tests/unit/test_tools_executor.py`

**依赖：** T3

**步骤：**

1. 构造 confirmer 计数器，第一次返回 `SESSION`，若再次调用则让测试失败或返回拒绝。
2. 使用同一个 `ToolExecutor` 和 `PermissionManager` 依次执行 `git status`、`git diff`。
3. 断言两个工具结果均成功、命令工具执行次数为 2、confirmer 只调用 1 次。
4. 保持现有异步等待测试，确认 `prepare()` 仍会等待用户选择而不是自动拒绝。
5. 保持黑名单拒绝时工具执行次数为零以及结构化来源断言。

**验证：** 运行 `uv run pytest tests/unit/test_tools_executor.py -q`，期望全部通过。

## T6：更新终端授权提示

**文件：** `src/okcode/terminal.py`、`tests/unit/test_terminal.py`

**依赖：** T1

**步骤：**

1. 将权限确认选项文本中的 `s=本会话` 改为 `s=本会话内允许此工具`。
2. 不修改 `d/o/s/p` 和 `/exit` 的输入映射。
3. 在终端测试中断言确认输出包含“本会话内允许此工具”。
4. 保留 EOF、中断、未知输入安全拒绝和权限模式工具栏测试。

**验证：** 运行 `uv run pytest tests/unit/test_terminal.py -q`，期望全部通过；运行 `uv run ruff check src/okcode/terminal.py tests/unit/test_terminal.py`。

## T7：验证父子 Agent 会话授权隔离

**文件：** `tests/unit/test_agents_runner.py`；必要时最小修改 `src/okcode/agents/runtime.py`

**依赖：** T2、T3

**步骤：**

1. 将依赖 `_session_rules` 的旧断言更新为 `_session_grants` 或行为级断言。
2. 让父管理器先获得一条会话授权，再创建子 Agent 权限管理器，验证子侧没有继承该授权。
3. 让子 Agent 触发自己的 `SESSION` 分支，验证父管理器的授权集合没有新增内容。
4. 确认 Shared/Worktree 子 Agent 都由新管理器实例限定工作区，不共享父会话内存状态。
5. 只有现有 `_clone_permissions()` 因类型调整不能工作时，才修改 `src/okcode/agents/runtime.py`，且不得复制 `_session_grants`。

**验证：** 运行 `uv run pytest tests/unit/test_agents_runner.py -q`，期望全部通过。

## T8：运行权限与交互定向回归

**文件：** 无新增修改；若失败，只修复与本次变更直接相关的问题

**依赖：** T4、T5、T6、T7

**步骤：**

1. 运行权限黑名单、规则、管理器、执行器测试。
2. 运行 Conversation、Terminal、CLI 和 Hook 相关测试。
3. 运行 Agent Runner 测试，确认权限隔离未回归。
4. 对失败逐项定位，不通过扩大 `allow` 范围或放松拒绝断言规避问题。

**验证：**

```powershell
uv run pytest tests/unit/test_permissions_blacklist.py tests/unit/test_permissions_rules.py tests/unit/test_permissions_manager.py tests/unit/test_tools_executor.py -q
uv run pytest tests/unit/test_conversation.py tests/unit/test_terminal.py tests/unit/test_cli.py tests/unit/test_hooks_actions.py tests/unit/test_agents_runner.py -q
```

期望两组测试全部通过。

## T9：执行完整质量检查

**文件：** 全部本次修改文件

**依赖：** T8

**步骤：**

1. 运行 Ruff 格式检查和静态检查。
2. 运行完整测试集。
3. 运行 `git diff --check`。
4. 检查最终 `git status --short` 和差分，确认无临时脚本、YAML 授权文件、调试输出或无关改动。
5. 按 `checklist.md` 逐项记录实际验收证据。

**验证：**

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
git diff --check
git status --short
```

期望所有命令通过，工作树中只包含本次功能代码、测试和四份阶段文档。

## 执行顺序

```text
T1
 ├─> T2 -> T3 -> T4 ─┐
 ├─> T6              ├─> T8 -> T9
 └────────> T5 ──────┤
             T7 ─────┘
```

