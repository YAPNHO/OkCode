# 会话级权限授权修复 Checklist

> 每项都通过运行测试、检查结构化结果或观察终端文本来验收。开发完成后逐项记录实际证据，不以代码阅读代替行为验证。

> 验收结果（2026-08-03）：47/47 项通过。补充使用锁定的 Ruff 0.16.0 格式化 10 个既有文件后，全仓库 `ruff format --check .` 已通过；本次会话权限修复的 7 个代码与测试文件仍通过全部检查。

## 实现完整性

- [x] 会话授权键只包含授权域、工具名和权限目标类型，不保存完整命令、路径、参数或工作区绝对路径。（验证：运行 `uv run pytest tests/unit/test_permissions_manager.py -q`，检查授权键构造与生命周期测试通过。）
- [x] 普通 Agent 工具调用和 Hook shell 命令使用不同授权域；同名 `run_command` 不会跨域共享授权。（验证：普通命令获得 `SESSION` 后调用 Hook 命令，期望仍进入 Hook 自身权限决策。）
- [x] 同一普通命令工具的不同命令文本共享会话授权。（验证：`git status` 选择 `SESSION` 后调用 `git diff`，期望第二次来源为 `session`，confirmer 总次数为 1。）
- [x] 同一路径工具的不同工作区内目标共享会话授权。（验证：`write_file(src/a.py)` 选择 `SESSION` 后预检 `write_file(src/b.py)`，期望第二次不调用 confirmer。）
- [x] 没有权限目标的工具按同一授权域和工具名复用会话授权，不影响其他工具。（验证：构造 `PermissionTargetKind.NONE` 工具的两个调用，期望第二次来源为 `session`；另一个工具仍按默认模式处理。）
- [x] 重复选择同一会话授权不会产生重复状态或改变决策结果。（验证：检查 `_session_grants` 使用集合去重，并运行重复授权测试。）
- [x] “仅本次允许”不创建会话授权。（验证：连续两次同一工具调用，第一次返回 `ONCE`，期望第二次再次调用 confirmer。）
- [x] “永久允许”继续生成完整目标精确规则，不扩大为工具级永久授权。（验证：永久允许 `git status` 后重建管理器，`git status` 命中项目本地规则，`git diff` 不命中该规则。）
- [x] 会话授权不写入用户、项目或项目本地权限 YAML。（验证：选择 `SESSION` 前后比较三个路径，期望不存在或内容完全不变。）
- [x] 重新创建 `PermissionManager` 后会话授权消失。（验证：使用同一工作区构造新管理器，期望相同调用重新按 `default` 进入确认。）
- [x] `/clear` 的现有进程级权限行为未被本次修复额外改变。（验证：Conversation 相关测试保持通过，代码没有在 `reset_session()` 新增授权清理逻辑。）

## 安全优先级

- [x] 已有普通命令会话授权时，危险命令仍由黑名单拒绝。（验证：先授权 `run_command`，再预检安全测试样本中的危险命令，期望 `allowed=false`、来源为 `blacklist`。）
- [x] 已有路径工具会话授权时，工作区外路径仍由沙箱拒绝。（验证：调用工作区外目标，期望错误码为 `outside_workspace`、来源为 `sandbox`、工具执行次数为零。）
- [x] 通过符号链接或 Windows 重解析点逃逸工作区的路径仍被拒绝。（验证：运行工作区既有符号链接测试；平台不支持创建链接时只允许跳过对应平台项。）
- [x] 持久化规则选中的 `deny` 在已有会话授权下仍终局拒绝。（验证：会话授权 `run_command` 后调用命中项目本地 deny 的命令，期望保留 `project_local` 来源且工具不执行。）
- [x] 持久化 `allow` 和 `allow` 模式都不能绕过黑名单或沙箱。（验证：运行黑名单与权限管理器既有组合测试。）
- [x] 普通工具域授权不能让后台 Hook shell 自动执行。（验证：普通 `run_command` 获得 `SESSION` 后执行 `authorize_hook_command_async(..., background=True)`，期望拒绝。）
- [x] Hook 域自己获得会话授权后只在 Hook 域复用，且危险命令仍被黑名单拒绝。（验证：两个不同安全 Hook 命令只确认一次；随后危险 Hook 命令仍拒绝。）
- [x] 子 Agent 与 Worktree 权限管理器不继承或回写父会话授权。（验证：运行 `uv run pytest tests/unit/test_agents_runner.py -q`，检查父子授权集合隔离断言通过。）

## 可观察行为与集成

- [x] 终端确认提示明确显示 `s=本会话内允许此工具`。（验证：运行终端测试并检查捕获文本。）
- [x] `d/o/s/p` 和 `/exit` 的输入映射保持拒绝、仅本次、本会话、永久、正常退出语义。（验证：运行 `test_permission_confirmation_maps_choices_and_safe_failures_to_deny`。）
- [x] 同一个 `ToolExecutor` 连续执行两个不同安全命令时，两个结果均成功、工具执行两次、确认仅一次。（验证：运行新增执行器回归测试。）
- [x] 权限拒绝仍发生在工具副作用之前。（验证：黑名单、沙箱和 deny 场景的计数型工具执行次数均为零。）
- [x] 权限拒绝结果继续包含稳定的 `permission_source` 和 `executed=false`，且不泄露工作区外绝对路径。（验证：检查 `ToolExecutionResult.data` 和终端渲染测试。）
- [x] 异步权限确认仍会等待用户选择，不会因事件循环异常自动拒绝。（验证：运行执行器异步等待测试，确认 release 前任务未完成、release 后成功。）
- [x] 连续工具调用的请求、开始和完成事件顺序保持稳定。（验证：运行 Conversation 多工具调度测试，期望原有顺序断言通过。）
- [x] `/permission strict|default|allow` 与工具栏权限模式显示没有变化。（验证：运行命令处理、Conversation 和 Terminal 权限模式测试。）
- [x] CLI 仍只向正式父会话执行器注入同一个权限管理器，未新增配置字段或磁盘格式。（验证：运行 `uv run pytest tests/unit/test_cli.py -q`，并检查本次差分没有配置 Schema 或规则加载格式修改。）

## Spec 验收标准

- [x] AC1：`git status` 选择“本会话允许”后，`git diff` 无需再次确认且正常执行。（验证：权限管理器与执行器回归测试均通过。）
- [x] AC2：路径工具跨工作区内目标复用授权，工作区外目标仍被沙箱拒绝且执行次数为零。（验证：路径会话授权测试通过。）
- [x] AC3：会话授权只存在于当前管理器内存，新管理器重新确认，YAML 不变。（验证：生命周期和文件内容对比测试通过。）
- [x] AC4：危险命令在会话授权、`allow` 模式和持久化 allow 下均由黑名单拒绝。（验证：黑名单组合测试通过。）
- [x] AC5：显式 `deny` 在已有会话授权下仍按原来源拒绝且工具不执行。（验证：规则优先级回归测试通过。）
- [x] AC6：`ONCE` 不复用，`PERMANENT` 只对精确持久化目标生效。（验证：确认范围测试通过。）
- [x] AC7：Agent Loop、异步确认、工具事件顺序及权限/终端测试保持通过。（验证：运行相关集成测试组。）
- [x] AC8：权限定向测试、相关集成测试、本次修改文件的 Ruff 格式检查、Ruff 静态检查、全量测试和 `git diff --check` 全部通过。（验证：执行下方完整命令集及本次文件定向格式检查。）

## 自动化测试与质量检查

- [x] 权限核心测试通过。（实际：33 项通过；补充去重断言后，`test_permissions_manager.py` 单文件 12 项通过。）
- [x] Conversation、终端、CLI、Hook 和子 Agent 集成测试通过。（实际：73 项通过。）
- [x] 全仓库 Ruff 格式检查通过。（实际：使用 Ruff 0.16.0 格式化原有 10 个文件后，`uv run ruff format --check .` 输出 `271 files already formatted`，退出码为 0。）
- [x] Ruff 静态检查通过。（实际：`uv run ruff check .` 退出码为 0。）
- [x] 全量测试通过且不访问真实模型服务、不执行真实危险命令。（实际：460 项通过。）
- [x] 最终差分无空白错误、临时授权 YAML、调试输出或无关改动。（实际：`git diff --check` 通过，文件清单已人工核对。）

```powershell
uv run pytest tests/unit/test_permissions_blacklist.py tests/unit/test_permissions_rules.py tests/unit/test_permissions_manager.py tests/unit/test_tools_executor.py -q
uv run pytest tests/unit/test_conversation.py tests/unit/test_terminal.py tests/unit/test_cli.py tests/unit/test_hooks_actions.py tests/unit/test_agents_runner.py -q
uv run ruff format --check .
uv run ruff check .
uv run pytest -q
git diff --check
git status --short
```

## 端到端场景

- [x] 场景 1——连续不同命令：在 `default` 模式下，Agent 请求 `run_command(git status)`，用户输入 `s`，随后 Agent 请求 `run_command(git diff)`。实际：终端只需一次权限确认，两条调用均成功，第二次权限来源为 `session`。
- [x] 场景 2——路径授权仍受沙箱保护：用户对 `write_file(src/a.py)` 输入 `s`，随后调用 `write_file(src/b.py)` 和工作区外路径。实际：工作区内目标复用授权；工作区外目标被拒绝，没有文件副作用。
- [x] 场景 3——显式拒绝不可绕过：先让 `run_command` 获得会话授权，再调用命中项目本地 `deny` 的命令。实际：拒绝来源仍为项目本地规则，工具未执行。
- [x] 场景 4——Hook 授权域隔离：普通 `run_command` 获得会话授权后触发后台 Hook shell。实际：后台 Hook 未被普通工具授权放行；Hook 域单独授权后可复用，危险 Hook 命令仍被黑名单拒绝。
- [x] 场景 5——生命周期隔离：在一个管理器中建立会话授权后重建管理器并创建子 Agent。实际：新管理器和子 Agent 均未继承父会话授权，父管理器状态也未被子侧修改。
