# OkCode 第十三阶段：子 Agent Worktree 隔离 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | src/okcode/worktrees/__init__.py | 导出 worktree 子系统核心模型和管理器 |
| 新建 | src/okcode/worktrees/models.py | 定义 worktree 身份、元数据、租约、初始化报告、退出报告、清理报告 |
| 新建 | src/okcode/worktrees/naming.py | 校验 worktree 安全名称并派生默认目录名和分支名 |
| 新建 | src/okcode/worktrees/git.py | 封装 Git worktree、状态、HEAD、upstream、ahead 检查 |
| 新建 | src/okcode/worktrees/metadata.py | 读写并校验 .okcode/worktree.json |
| 新建 | src/okcode/worktrees/initializer.py | 幂等复制配置、配置 hooks、链接依赖、补齐运行文件 |
| 新建 | src/okcode/worktrees/manager.py | 编排创建、快速恢复、退出、删除和过期清理 |
| 新建 | src/okcode/worktrees/cleanup.py | 后台清理 Worker |
| 新建 | src/okcode/agents/runtime.py | 为子 Agent 按目标工作区构造运行依赖 |
| 修改 | src/okcode/agents/models.py | 增加隔离枚举、请求字段、结果字段和快照字段 |
| 修改 | src/okcode/agents/roles.py | 解析角色 frontmatter 的 isolation 字段 |
| 修改 | src/okcode/agents/tool.py | Agent 工具 schema 接受 isolation 与 worktree_name |
| 修改 | src/okcode/agents/launcher.py | 合并隔离策略并构造 WorktreePrepareRequest |
| 修改 | src/okcode/agents/runner.py | 在子 Agent 运行前 prepare worktree，结束后 finalize |
| 修改 | src/okcode/agents/manager.py | 后台任务快照携带 worktree 状态 |
| 修改 | src/okcode/agents/notifications.py | 完成通知携带 worktree 路径、分支和清理结果 |
| 修改 | src/okcode/agents/__init__.py | 导出必要的新模型或保持导出兼容 |
| 修改 | src/okcode/tools/defaults.py | 支持按 Workspace 重建本地工具注册表 |
| 修改 | src/okcode/cli.py | 组装 WorktreeManager、runtime factory 和 cleanup worker |
| 修改 | .gitignore | 确认 .okcode/worktrees 仍被忽略，必要时补充更明确规则 |
| 新建 | tests/unit/test_worktrees_naming.py | worktree 名称和分支派生测试 |
| 新建 | tests/unit/test_worktrees_metadata.py | 元数据读写与恢复校验测试 |
| 新建 | tests/unit/test_worktrees_git.py | Git client 状态解析和命令封装测试 |
| 新建 | tests/unit/test_worktrees_initializer.py | 环境初始化幂等和 Windows 降级测试 |
| 新建 | tests/unit/test_worktrees_manager.py | 创建、恢复、退出、删除保护测试 |
| 新建 | tests/unit/test_worktrees_cleanup.py | 三层过滤和过期清理测试 |
| 修改 | tests/unit/test_agents_roles.py | 增加 isolation frontmatter 解析测试 |
| 修改 | tests/unit/test_agents_launcher.py | 增加隔离策略合并和 worktree 请求测试 |
| 修改 | tests/unit/test_agents_runner.py | 增加 prepare/finalize 调用与错误隔离测试 |
| 修改 | tests/unit/test_agents_manager.py | 增加后台快照 worktree 字段测试 |
| 修改 | tests/unit/test_agents_notifications.py | 增加 worktree 通知摘要测试 |
| 修改 | tests/unit/test_cli.py | 增加 WorktreeManager 和 cleanup worker 组装测试 |
| 新建 | tests/integration/test_subagent_worktree.py | 临时 Git 仓库端到端隔离测试 |

## T1：建立 worktree 纯模型

**文件：** src/okcode/worktrees/models.py、src/okcode/worktrees/__init__.py  
**依赖：** 无  
**步骤：**
1. 定义 WorktreeIdentity，包含 name、branch、task_id、parent_session_id、role_name、trigger。
2. 定义 WorktreeMetadata，包含版本、主仓库路径、common dir、managed_root、worktree_path、identity、base_ref、base_head、时间字段和初始化摘要。
3. 定义 WorktreePrepareRequest、WorktreeLease、WorktreeInitializationReport、WorktreeExitReport、WorktreeCleanupReport。
4. 定义 WorktreeProtectionReason 和 cleanup decision 相关枚举。
5. 在 __init__.py 导出后续模块需要的公开类型。

**验证：** 运行 uv run pytest tests/unit/test_worktrees_metadata.py -q，若测试文件尚未创建，可先运行 uv run python -c "from okcode.worktrees.models import WorktreeIdentity; print(WorktreeIdentity)"，期望导入成功。

## T2：实现安全名称校验

**文件：** src/okcode/worktrees/naming.py、tests/unit/test_worktrees_naming.py  
**依赖：** T1  
**步骤：**
1. 实现 validate_worktree_name，允许字母、数字、短横线、下划线、点号和正斜杠。
2. 拒绝空字符串、空段、单点段、双点段、反斜杠、盘符、绝对路径、父级回退、控制字符和非法字符。
3. 实现单段长度和整体长度上限。
4. 实现 derive_agent_worktree_name，按 agents/<role-or-fork>/<task-id-short> 派生默认名称。
5. 实现 derive_agent_branch_name，生成 okcode/agents/<safe-name> 形式的分支名。
6. 编写单元测试覆盖合法嵌套、非法路径遍历、超长名称和默认派生。

**验证：** 运行 uv run pytest tests/unit/test_worktrees_naming.py -q，期望全部通过。

## T3：实现 Git worktree client

**文件：** src/okcode/worktrees/git.py、tests/unit/test_worktrees_git.py  
**依赖：** T1、T2  
**步骤：**
1. 定义 GitWorktreeClient，所有方法显式接收 cwd 或 repo_root。
2. 实现 repo_common_dir、resolve_head、check_ref_format。
3. 实现 create_worktree 和 remove_worktree，命令使用 git worktree add/remove，显式 cwd，不调用 chdir。
4. 实现 list_worktrees，解析 git worktree list --porcelain。
5. 实现 status_porcelain，解析 git status --porcelain=v1 输出为 staged、modified、deleted、untracked 摘要。
6. 实现 has_upstream 和 ahead_count，用于删除保护判断。
7. 单元测试用临时 Git 仓库覆盖 add/list/status/remove，状态解析可用纯字符串单测补充。

**验证：** 运行 uv run pytest tests/unit/test_worktrees_git.py -q，期望全部通过。

## T4：实现元数据读写与校验

**文件：** src/okcode/worktrees/metadata.py、tests/unit/test_worktrees_metadata.py  
**依赖：** T1  
**步骤：**
1. 实现 metadata_path(worktree_path) 定位 .okcode/worktree.json。
2. 实现 write_metadata，创建 .okcode 目录并写入 UTF-8 JSON。
3. 实现 read_metadata，缺失、JSON 错误、版本不支持时返回结构化错误。
4. 实现 validate_metadata，校验 repo_root、repo_common_dir、managed_root、worktree_path、identity.name、identity.branch、task_id。
5. 编写测试覆盖正常读写、元数据缺失、路径不匹配、身份不匹配、版本不支持。

**验证：** 运行 uv run pytest tests/unit/test_worktrees_metadata.py -q，期望全部通过。

## T5：实现环境初始化规则

**文件：** src/okcode/worktrees/initializer.py、tests/unit/test_worktrees_initializer.py  
**依赖：** T1、T4  
**步骤：**
1. 定义 WorktreeInitializationRules，包含允许复制文件、允许链接目录、hooks 策略和补齐文件规则。
2. 实现默认复制 allowlist：config.yaml、.okcode/config.yaml、.okcode/permissions.yaml、.okcode/permissions.local.yaml、.okcode/mcp.yaml、.okcode/hooks.yaml。
3. 复制文件时只在目标不存在或内容一致时写入；目标已修改时跳过并记录 warning。
4. 实现 hooks 配置：读取主仓库 core.hooksPath，能转为 worktree 可访问路径时配置；否则记录 warning。
5. 实现大型依赖目录链接：默认候选 .venv、.venv312、node_modules；Windows 链接失败时跳过并记录 warning。
6. 实现被忽略运行文件补齐，只按规则复制明确文件，不递归复制整个 ignored 目录。
7. 编写测试覆盖首次初始化、重复初始化、目标已修改、链接失败降级、hooks warning。

**验证：** 运行 uv run pytest tests/unit/test_worktrees_initializer.py -q，期望全部通过。

## T6：实现 WorktreeManager 创建与快速恢复

**文件：** src/okcode/worktrees/manager.py、tests/unit/test_worktrees_manager.py  
**依赖：** T1-T5  
**步骤：**
1. 实现 WorktreeManager 构造函数，接收 repo_root、managed_root、GitWorktreeClient、WorktreeInitializer。
2. 实现 prepare：校验名称和分支，解析 managed_root 下的目标路径。
3. 目标目录不存在时，解析 base_head，调用 git worktree add，写入元数据，执行初始化。
4. 目标目录存在时，只读读取元数据并校验身份，校验通过则复用并初始化，不调用 git worktree add。
5. 目录存在但元数据缺失或不匹配时，返回结构化错误，不覆盖目录。
6. 返回 WorktreeLease，包含 created、recovered、initialization_report 和 prompt_note。
7. 编写测试覆盖新建、快速恢复、不调用 git、元数据不匹配拒绝。

**验证：** 运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "prepare or recover"，期望全部通过。

## T7：实现退出、删除保护和显式删除

**文件：** src/okcode/worktrees/manager.py、tests/unit/test_worktrees_manager.py  
**依赖：** T6  
**步骤：**
1. 实现 inspect，读取元数据和 Git 状态，生成 WorktreeExitReport。
2. 实现保护判断：工作区修改、暂存区修改、未跟踪文件、未推送提交、无 upstream 且 HEAD 变化、元数据不匹配、路径越界、Git 状态失败。
3. 实现 finalize，无保护原因时自动 git worktree remove，有保护原因时保留并返回原因。
4. 实现 delete(name, force=False)，默认遵守同一保护规则。
5. force=True 时仍必须校验 managed_root、元数据和 Git worktree 匹配，只跳过变更类保护。
6. 编写测试覆盖无变更自动删除、未提交保留、未跟踪保留、HEAD 变化且无 upstream 保留、force 仍拒绝非管理目录。

**验证：** 运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "finalize or delete or protection"，期望全部通过。

## T8：实现后台过期清理

**文件：** src/okcode/worktrees/manager.py、src/okcode/worktrees/cleanup.py、tests/unit/test_worktrees_cleanup.py  
**依赖：** T7  
**步骤：**
1. 定义 WorktreeCleanupPolicy，包含过期时间、扫描间隔、每次最大清理数量。
2. 实现 cleanup_expired，扫描 managed_root 下候选目录。
3. 对每个候选执行三层过滤：路径在 managed_root、元数据存在且匹配、Git worktree list 能匹配。
4. 过期且三层过滤通过后走 delete 默认保护。
5. 实现 WorktreeCleanupWorker，支持 start 和 close，关闭时不留下后台任务。
6. 编写测试覆盖过期删除、未过期跳过、缺元数据跳过、Git 列表不匹配跳过、有变更保护跳过。

**验证：** 运行 uv run pytest tests/unit/test_worktrees_cleanup.py -q，期望全部通过。

## T9：扩展 Agent 模型

**文件：** src/okcode/agents/models.py、src/okcode/agents/__init__.py  
**依赖：** T1  
**步骤：**
1. 新增 AgentIsolationMode，包含 SHARED 和 WORKTREE。
2. AgentRole 增加 isolation 字段，默认 shared。
3. AgentToolRequest 增加 isolation 和 worktree_name 可选字段。
4. AgentLaunchRequest 增加 isolation、worktree_request、main_workspace_root 字段。
5. AgentTaskResult 和 AgentTaskSnapshot 增加 isolation 和 worktree 字段。
6. 更新 __init__.py 导出，保证现有导入不破坏。

**验证：** 运行 uv run pytest tests/unit/test_agents_roles.py tests/unit/test_agents_launcher.py -q，期望现有用例不因字段默认值失败。

## T10：解析角色 isolation frontmatter

**文件：** src/okcode/agents/roles.py、tests/unit/test_agents_roles.py  
**依赖：** T9  
**步骤：**
1. 在 _ROOT_FIELDS 中加入 isolation。
2. 实现 isolation 字段解析，允许 shared、worktree，未提供时默认 shared。
3. 非法值抛 ConfigError，错误包含路径、角色名和字段名。
4. 更新合法角色测试，确认旧角色不写 isolation 仍能加载。
5. 增加 worktree 角色测试和非法值测试。

**验证：** 运行 uv run pytest tests/unit/test_agents_roles.py -q，期望全部通过。

## T11：扩展 Agent 工具请求 schema

**文件：** src/okcode/agents/tool.py、tests/unit/test_agents_tool.py  
**依赖：** T9  
**步骤：**
1. 在 Agent 工具 JSON schema 中加入 isolation 和 worktree_name 可选字段。
2. isolation 只允许 shared 或 worktree。
3. 保持工具名称、顶层结构和 defined/fork oneOf 兼容。
4. 更新请求解析逻辑，让缺省字段为 None。
5. 编写测试覆盖旧请求兼容、defined worktree 请求、fork worktree 请求、非法 isolation。

**验证：** 运行 uv run pytest tests/unit/test_agents_tool.py -q，期望全部通过。

## T12：在 Launcher 合并隔离策略

**文件：** src/okcode/agents/launcher.py、tests/unit/test_agents_launcher.py  
**依赖：** T2、T9-T11  
**步骤：**
1. 在 launch_from_tool 和 launch_from_hook 路径中计算 effective isolation。
2. defined 默认使用角色 isolation；fork 默认 shared。
3. 请求 isolation=worktree 可升级为 worktree；请求 isolation=shared 不能降低角色 worktree。
4. 生成 task_id 后，通过 naming 模块派生 worktree name 和 branch。
5. 如果请求提供 worktree_name，先校验再使用。
6. 构造 WorktreePrepareRequest，写入 AgentLaunchRequest。
7. 编写测试覆盖角色默认 shared、角色 worktree、请求升级、降级无效、非法 name 拒绝、Hook 沿用角色 isolation。

**验证：** 运行 uv run pytest tests/unit/test_agents_launcher.py -q，期望全部通过。

## T13：实现子 Agent runtime factory

**文件：** src/okcode/agents/runtime.py、src/okcode/tools/defaults.py、tests/unit/test_agents_runner.py  
**依赖：** T9  
**步骤：**
1. 在 tools/defaults.py 中拆出按 Workspace 创建本地工具的工厂，避免复用主工作区文件工具实例。
2. 新建 ChildAgentRuntime 数据结构，包含 workspace、registry、executor、permissions、context_manager、context_factory、hooks 等运行依赖。
3. 实现 build_child_runtime(workspace_root, launch)，用 Workspace(workspace_root) 创建路径绑定工具。
4. 过滤工具时保留第十二阶段多层过滤结果，不重新放开被拒工具。
5. PermissionManager、InstructionLoader、MemoryStore、ContextManager、RuntimePromptContextFactory 都绑定 workspace_root。
6. MCP 工具和 Provider 配置继续共享，但路径相关对象重建。
7. 编写测试用两个临时目录验证相同相对文件路径读取不同内容。

**验证：** 运行 uv run pytest tests/unit/test_agents_runner.py -q -k "runtime or workspace"，期望全部通过。

## T14：Runner 接入 prepare/finalize

**文件：** src/okcode/agents/runner.py、tests/unit/test_agents_runner.py  
**依赖：** T6、T7、T13  
**步骤：**
1. AgentRunner 构造函数接收 WorktreeManager 和 child runtime factory。
2. 运行子 Agent 前，如果 launch.isolation 为 worktree，调用 WorktreeManager.prepare。
3. 将 lease.prompt_note 注入子 Agent 环境说明，只注入子 Agent，不改主 Agent 普通系统提示。
4. 用 lease.path 创建子运行时；shared 模式继续使用父工作区根。
5. 在 try/finally 中保证完成、失败、取消和超时时都调用 finalize。
6. finalize 结果写入 AgentTaskResult.worktree。
7. prepare 失败时返回结构化任务失败，不启动模型请求。
8. 编写测试覆盖 prepare 成功、prepare 失败、运行失败仍 finalize、finalize 失败保留错误。

**验证：** 运行 uv run pytest tests/unit/test_agents_runner.py -q，期望全部通过。

## T15：后台任务和通知展示 worktree 状态

**文件：** src/okcode/agents/manager.py、src/okcode/agents/notifications.py、tests/unit/test_agents_manager.py、tests/unit/test_agents_notifications.py  
**依赖：** T9、T14  
**步骤：**
1. AgentTaskManager 生成快照时携带 isolation 和 worktree 摘要。
2. 任务列表展示路径、分支、清理状态和保留原因的短文本。
3. 完成通知中加入 worktree 路径、分支、cleanup decision、protection reasons。
4. 保持没有 worktree 时的通知文本兼容，不显示空噪声字段。
5. 编写测试覆盖 completed/failed/cancelled/timed_out 都能携带 worktree 退出报告。

**验证：** 运行 uv run pytest tests/unit/test_agents_manager.py tests/unit/test_agents_notifications.py -q，期望全部通过。

## T16：CLI 组装 WorktreeManager 和 CleanupWorker

**文件：** src/okcode/cli.py、tests/unit/test_cli.py  
**依赖：** T8、T13、T14  
**步骤：**
1. CLI 创建主 Workspace 后创建 WorktreeManager，managed_root 为 workspace.root/.okcode/worktrees。
2. 创建 WorktreeCleanupWorker，并在应用生命周期内启动。
3. AgentRunner 初始化时注入 WorktreeManager 和 child runtime factory。
4. finally 中关闭 cleanup worker，异常时不影响 provider、mcp、memory worker 的既有清理。
5. 更新 CLI 组装测试，验证未启用子 Agent 或 worktree 时普通启动路径不变。

**验证：** 运行 uv run pytest tests/unit/test_cli.py -q，期望全部通过。

## T17：确认 .gitignore 与受管理目录

**文件：** .gitignore、tests/unit/test_worktrees_manager.py  
**依赖：** T6  
**步骤：**
1. 检查当前 .gitignore 的 .okcode/* 是否覆盖 .okcode/worktrees。
2. 如需更清晰，补充 .okcode/worktrees/ 规则，但不破坏已允许追踪的 .okcode/permissions.yaml。
3. 在 manager 测试中断言 managed_root 位于 repo_root/.okcode/worktrees。
4. 确认不会把 worktree 目录加入版本控制。

**验证：** 运行 git check-ignore .okcode/worktrees/example，期望显示被忽略；运行 uv run pytest tests/unit/test_worktrees_manager.py -q，期望通过。

## T18：端到端 worktree 隔离测试

**文件：** tests/integration/test_subagent_worktree.py  
**依赖：** T10-T17  
**步骤：**
1. 创建临时 Git 仓库并提交基准文件。
2. 配置一个 isolation=worktree 的测试角色。
3. 用假 Provider 驱动子 Agent 读取和修改文件。
4. 断言主工作区文件未被修改，worktree 文件发生预期变化。
5. 断言任务结果包含 worktree 路径、分支和保留原因。
6. 增加无变更子 Agent 场景，断言 worktree 被自动清理。
7. 增加非法 worktree_name 场景，断言拒绝且没有创建目录。

**验证：** 运行 uv run pytest tests/integration/test_subagent_worktree.py -q，期望全部通过。

## T19：兼容性回归测试

**文件：** tests/unit/test_agents_*.py、tests/integration/test_subagent_turn.py  
**依赖：** T18  
**步骤：**
1. 运行全部子 Agent 单元测试，确认 shared 默认行为不变。
2. 运行第十二阶段子 Agent 端到端测试，确认未声明 isolation 的角色仍走共享工作区。
3. 运行 hooks subagent 测试，确认 Hook 默认沿用角色 isolation，不破坏占位升级后的真实启动路径。
4. 修复因新增字段导致的 dataclass 构造、快照渲染、通知文本断言失败。

**验证：** 运行 uv run pytest tests/unit/test_agents_*.py tests/integration/test_subagent_turn.py -q，期望全部通过。

## T20：全量验证与格式检查

**文件：** 全项目  
**依赖：** T19  
**步骤：**
1. 运行 uv run pytest -q。
2. 运行 uv run ruff format。
3. 运行 uv run ruff check。
4. 运行 git diff --check。
5. 检查 git status，确认只包含本阶段预期文件。

**验证：** pytest、ruff format、ruff check、git diff --check 全部通过。

## 执行顺序

    T1 -> T2 -> T3 -> T4 -> T5
              \       \       \
               -> T6 -> T7 -> T8
    T9 -> T10 -> T11 -> T12 -> T13 -> T14 -> T15 -> T16 -> T17 -> T18 -> T19 -> T20

T1-T8 先完成独立 worktree 基础设施。T9-T17 再接入子 Agent 运行链路。T18-T20 做端到端验证和全量回归。

## 分组验证建议

- 基础设施阶段：uv run pytest tests/unit/test_worktrees_*.py -q
- 子 Agent 接入阶段：uv run pytest tests/unit/test_agents_roles.py tests/unit/test_agents_tool.py tests/unit/test_agents_launcher.py tests/unit/test_agents_runner.py -q
- 通知与 CLI 阶段：uv run pytest tests/unit/test_agents_manager.py tests/unit/test_agents_notifications.py tests/unit/test_cli.py -q
- 端到端阶段：uv run pytest tests/integration/test_subagent_worktree.py tests/integration/test_subagent_turn.py -q
- 最终阶段：uv run pytest -q；uv run ruff format；uv run ruff check；git diff --check
