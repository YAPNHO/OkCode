# OkCode 第十三阶段：子 Agent Worktree 隔离 Plan

## 架构概览

本阶段新增 okcode.worktrees 子系统，负责 Git worktree 的安全命名、创建恢复、环境初始化、退出保护、显式删除和后台过期清理。子 Agent 子系统继续作为用户可见入口：角色 frontmatter 或 Agent 工具请求声明隔离需求后，AgentLauncher 把隔离信息写入启动请求，AgentRunner 在真正创建子会话前向 WorktreeManager 申请一个受管理 worktree。

整体分为九个组件：

- Worktree 模型：定义隔离模式、受管理目录身份、元数据、初始化报告、退出报告、删除保护原因和清理结果。
- 安全命名：校验模型或系统传入的 worktree 相对名称，限制字符集、层级、单段长度和整体长度。
- Git worktree 客户端：集中封装所有 Git 调用，所有命令显式传入 cwd，不使用进程级 chdir。
- Worktree 管理器：实现创建、只读快速恢复、进入、退出、显式删除和过期扫描的主流程。
- 环境初始化器：幂等复制允许的本地配置，配置 Git hooks，链接大型依赖目录，补齐被忽略但运行必需的文件。
- 子 Agent 启动扩展：解析角色与工具请求中的 isolation 字段，生成 worktree 名称、分支名和启动上下文。
- 子 Agent 运行时工厂：按目标工作区重建 Workspace、ToolRegistry、PermissionManager、HookRuntime、InstructionLoader、MemoryStore 和 ContextManager。
- 结果与通知扩展：把 worktree 路径、分支、变更摘要和清理结果写入子 Agent 结果、后台任务快照和完成通知。
- 清理 Worker：后台定期扫描 .okcode/worktrees，只清理同时通过三层安全过滤和删除保护的过期 worktree。

## 核心数据结构

### AgentIsolationMode

枚举，放在 src/okcode/agents/models.py，必要时从 okcode.worktrees.models 重新导出。

- SHARED：保持第十二阶段行为，子 Agent 与主 Agent 使用同一个工作区根。
- WORKTREE：子 Agent 启动前进入独立 Git worktree。

角色未声明时默认为 SHARED。工具请求未声明时继承角色隔离模式；工具请求声明 WORKTREE 时只能升级为隔离，不能绕过角色、权限或全局安全规则。

### WorktreeIdentity

数据结构，描述一个受管理 worktree 的稳定身份。

- name：通过安全校验后的相对名称，使用 POSIX 分隔符。
- branch：worktree 绑定的 Git 分支名。
- task_id：子 Agent 任务标识。
- parent_session_id：父会话标识。
- role_name：定义式角色名，可为空。
- trigger：tool 或 hook。

名称建议格式为 agents/<role-or-fork>/<task-id-short>。如果工具请求提供显式 worktree_name，也必须先通过同一套校验。

### WorktreeMetadata

写入每个 worktree 内部 .okcode/worktree.json 的持久元数据。

- version：元数据格式版本。
- repo_root：主工作区绝对路径。
- repo_common_dir：主仓库 Git common dir 绝对路径。
- managed_root：受管理 worktree 根目录绝对路径。
- worktree_path：当前 worktree 绝对路径。
- identity：WorktreeIdentity。
- base_ref：创建时使用的基准引用。
- base_head：创建时解析出的提交。
- created_at、last_used_at、expires_at。
- initialization：最近一次初始化报告摘要。

快速恢复只读取文件系统和该元数据，不调用 Git 创建命令，也不覆盖已有目录。元数据缺失、路径不匹配、仓库不匹配或任务身份不匹配时拒绝恢复。

### WorktreePrepareRequest

AgentRunner 传给 WorktreeManager 的创建或恢复请求。

- identity：WorktreeIdentity。
- main_workspace：主工作区绝对路径。
- base_ref：默认 HEAD。
- ttl_seconds：过期清理时间。
- initialization_policy：环境初始化规则集合。

### WorktreeLease

WorktreeManager.prepare 返回的运行期租约。

- path：worktree 绝对路径。
- branch：分支名。
- metadata：WorktreeMetadata。
- created：本次是否新建。
- recovered：本次是否快速恢复。
- initialization_report：WorktreeInitializationReport。
- prompt_note：注入子 Agent 上下文的路径说明。

### WorktreeInitializationReport

初始化结果。

- copied_files：已复制的配置或运行文件。
- linked_directories：已软链、目录连接或跳过的大型依赖目录。
- hook_mode：inherited、configured、copied、skipped。
- warnings：Windows 链接失败、源文件缺失、规则跳过等非致命提示。

### WorktreeExitReport

子 Agent 退出时生成。

- path、branch、name。
- status_summary：Git 状态摘要。
- changed_files：限长文件列表。
- protection_reasons：删除保护原因。
- cleanup_decision：removed、kept、failed。
- cleanup_message：返回给主 Agent 的可行动说明。

### WorktreeProtectionReason

枚举，覆盖删除保护。

- UNCOMMITTED_CHANGES：工作区或暂存区有修改。
- UNTRACKED_FILES：存在未跟踪文件。
- UNPUSHED_COMMITS：存在无法确认已推送的提交。
- UNKNOWN_UPSTREAM：有本地提交但无法确认 upstream。
- METADATA_MISMATCH：元数据与目标目录不匹配。
- OUTSIDE_MANAGED_ROOT：目标不在受管理目录。
- GIT_STATUS_FAILED：Git 状态无法确认。

实现层使用创建时记录的 base_head 辅助判断：无 upstream 但 HEAD 仍等于 base_head 时没有新增提交；无 upstream 且 HEAD 已变化时按未推送风险保留。

### WorktreeCleanupReport

后台清理单个目录的结果。

- candidate_path。
- filter_results：managed_path、metadata_present、git_worktree_match 三层过滤结果。
- expired：是否过期。
- decision：removed、skipped、failed。
- reason：跳过或失败原因。

## 核心接口

### WorktreeNameValidator

文件：src/okcode/worktrees/naming.py

职责：校验和规范化 worktree 相对名称。

接口：

- validate_worktree_name(raw: str) -> str
- validate_branch_component(raw: str) -> str
- derive_agent_worktree_name(role_name: str | None, task_id: str) -> str
- derive_agent_branch_name(name: str) -> str

校验规则：

- 只允许 A-Z、a-z、0-9、短横线、下划线、点号和正斜杠。
- 使用正斜杠分段，每段非空。
- 每段不能是单点或双点。
- 拒绝反斜杠、盘符、绝对路径、父级回退和控制字符。
- 单段默认不超过 64 字符，整体默认不超过 160 字符。
- 分支名额外通过 git check-ref-format --branch 或等价规则校验。

### GitWorktreeClient

文件：src/okcode/worktrees/git.py

职责：封装 Git 命令与状态解析，便于测试替换。

接口：

- repo_common_dir(cwd: Path) -> Path
- resolve_head(cwd: Path, ref: str = "HEAD") -> str
- create_worktree(repo_root: Path, path: Path, branch: str, base_ref: str) -> None
- remove_worktree(repo_root: Path, path: Path, force: bool = False) -> None
- list_worktrees(repo_root: Path) -> tuple[GitWorktreeEntry, ...]
- status_porcelain(path: Path) -> GitStatusSummary
- ahead_count(path: Path, base_head: str) -> int
- has_upstream(path: Path) -> bool
- check_ref_format(repo_root: Path, branch: str) -> bool

所有实现都通过子进程调用 Git，并显式设置 cwd。调用方不改变 Python 进程当前目录。

### WorktreeManager

文件：src/okcode/worktrees/manager.py

职责：对外提供完整生命周期。

接口：

- prepare(request: WorktreePrepareRequest) -> WorktreeLease
- finalize(lease: WorktreeLease, force_keep: bool = False) -> WorktreeExitReport
- delete(name: str, force: bool = False) -> WorktreeExitReport
- cleanup_expired(policy: WorktreeCleanupPolicy) -> tuple[WorktreeCleanupReport, ...]
- inspect(name: str) -> WorktreeExitReport

创建流程：

1. 校验 name 和 branch。
2. 解析受管理路径：<repo>/.okcode/worktrees/<name>。
3. 如果目录已存在，进入只读快速恢复流程：读取 .okcode/worktree.json、校验路径和身份、更新时间戳、执行幂等初始化，不调用 git worktree add。
4. 如果目录不存在，调用 git worktree add -b <branch> <path> <base_ref>。
5. 写入元数据。
6. 执行环境初始化。
7. 返回 WorktreeLease。

退出流程：

1. 调用 Git 状态检查。
2. 生成变更摘要和保护原因。
3. 如果没有保护原因且策略允许自动清理，调用 git worktree remove。
4. 删除成功后删除受管理目录残留；失败时保留并报告。
5. 无论清理与否，都返回 WorktreeExitReport。

### WorktreeInitializer

文件：src/okcode/worktrees/initializer.py

职责：让新 worktree 能运行 OkCode 和项目测试。

接口：

- initialize(lease: WorktreeLease, rules: WorktreeInitializationRules) -> WorktreeInitializationReport

默认规则：

- 复制允许的本地配置：config.yaml、.okcode/config.yaml、.okcode/permissions.yaml、.okcode/permissions.local.yaml、.okcode/mcp.yaml、.okcode/hooks.yaml，仅当源存在且目标不存在或内容相同。
- 配置 Git hooks：优先复用主仓库 core.hooksPath；相对 hooksPath 转成 worktree 可访问路径；无法可靠配置时记录 warning。
- 链接大型依赖目录：默认候选 .venv、.venv312、node_modules。Windows 上优先目录连接或符号链接，失败时跳过并记录 warning。
- 补齐被忽略运行文件：只按 allowlist 复制明确需要的文件，不递归复制整个 .okcode、缓存、密钥或任意 ignored 内容。

初始化必须幂等。若目标文件已被子 Agent 修改，不覆盖；若目标链接已存在且指向正确源，视为成功。

### WorktreeRuntimeFactory

文件：src/okcode/agents/runtime.py 或 src/okcode/agents/runner.py

职责：根据目标工作区创建子 Agent 的运行依赖。

接口：

- build_child_runtime(workspace_root: Path, launch: AgentLaunchRequest) -> ChildAgentRuntime

运行依赖包括：

- Workspace(workspace_root)。
- 工作区绑定的本地工具注册表。
- 过滤后的子 Agent 可见工具。
- 独立 PermissionManager，规则从该工作区路径加载。
- HookRuntime 使用相同规则语义，但 ActionRunner 绑定子 worktree 的 Workspace 和权限对象。
- InstructionLoader 从该 worktree 的指令路径加载项目指令。
- MemoryStore 从该 worktree 的记忆路径加载长期记忆。
- ContextManager 与 ArtifactStore 绑定该 worktree。
- RuntimePromptContextFactory 使用该 worktree 的绝对路径。

共享 MCP 工具、Provider 配置和无会话状态基础设施；路径绑定、权限状态、上下文状态、记忆读取和缓存状态按 worktree 重建。

### AgentRole frontmatter 扩展

文件：src/okcode/agents/roles.py

frontmatter 新增字段：

    isolation: worktree

允许值：

- shared：默认值，共享主工作区。
- worktree：为该角色启动独立 worktree。

解析层修改：

- _ROOT_FIELDS 增加 isolation。
- AgentRole 增加 isolation: AgentIsolationMode。
- 非法值报 ConfigError，错误包含文件、角色名和字段名。

### AgentToolRequest 扩展

文件：src/okcode/agents/tool.py、src/okcode/agents/models.py

新增可选字段：

- isolation：shared 或 worktree，可为空。
- worktree_name：可为空；提供时必须通过 WorktreeNameValidator。

规则：

- defined 子 Agent 默认使用角色 isolation。
- fork 子 Agent 默认 shared，除非请求显式 isolation=worktree。
- 请求 isolation=shared 不能降低角色 isolation=worktree。
- 后台 Hook subagent 沿用角色 isolation；Hook YAML 本阶段不额外暴露 worktree_name。

### AgentLaunchRequest 扩展

新增字段：

- isolation: AgentIsolationMode。
- worktree_request: WorktreePrepareRequest | None。
- main_workspace_root: Path。

AgentLauncher 在构造启动请求时生成 task_id 后再派生 worktree name 和 branch，保证名称稳定且不依赖模型自由文本。

### AgentTaskResult 和 AgentTaskSnapshot 扩展

新增字段：

- isolation: AgentIsolationMode。
- worktree: WorktreeExitReport | None。

后台任务查询和完成通知展示：

- 隔离模式。
- worktree 路径。
- 分支名。
- 清理状态。
- 保留原因。

## 模块设计

### src/okcode/worktrees/models.py

职责：定义 worktree 生命周期中的纯数据结构和枚举。  
对外接口：WorktreeIdentity、WorktreeMetadata、WorktreePrepareRequest、WorktreeLease、WorktreeInitializationReport、WorktreeExitReport、WorktreeProtectionReason、WorktreeCleanupReport。  
依赖：标准库 dataclasses、enum、pathlib、datetime。

### src/okcode/worktrees/naming.py

职责：所有目录名和分支名校验集中在这里，防止路径遍历。  
对外接口：validate_worktree_name、derive_agent_worktree_name、derive_agent_branch_name。  
依赖：re、pathlib；分支最终合法性可调用 GitWorktreeClient。

### src/okcode/worktrees/git.py

职责：Git 命令封装和 porcelain 输出解析。  
对外接口：GitWorktreeClient、GitWorktreeEntry、GitStatusSummary。  
依赖：asyncio 或 subprocess，Path。

### src/okcode/worktrees/metadata.py

职责：读写 .okcode/worktree.json，保证格式版本、路径和身份校验一致。  
对外接口：read_metadata、write_metadata、validate_metadata。  
依赖：json、dataclasses.asdict、Path。

### src/okcode/worktrees/initializer.py

职责：环境初始化规则和幂等执行。  
对外接口：WorktreeInitializationRules、WorktreeInitializer。  
依赖：shutil、os、Path、GitWorktreeClient。

### src/okcode/worktrees/manager.py

职责：创建、恢复、退出、删除和清理的编排入口。  
对外接口：WorktreeManager.prepare、finalize、delete、cleanup_expired、inspect。  
依赖：naming、metadata、git、initializer。

### src/okcode/worktrees/cleanup.py

职责：后台定期清理 Worker。  
对外接口：WorktreeCleanupWorker.start、close。  
依赖：WorktreeManager、asyncio。

### src/okcode/agents/models.py

职责：扩展第十二阶段子 Agent 模型。  
改动：新增 AgentIsolationMode；AgentRole、AgentToolRequest、AgentLaunchRequest、AgentTaskResult、AgentTaskSnapshot 增加隔离和 worktree 字段。

### src/okcode/agents/roles.py

职责：解析 isolation frontmatter。  
改动：允许 shared/worktree，非法值报 ConfigError；默认 shared 保持兼容。

### src/okcode/agents/launcher.py

职责：把工具请求或 Hook 请求转换成启动请求。  
改动：合并角色 isolation 和请求 isolation，生成 WorktreePrepareRequest，写入 AgentLaunchRequest。

### src/okcode/agents/runner.py

职责：实际运行子 Agent。  
改动：在创建子会话前调用 WorktreeManager.prepare；用 WorktreeRuntimeFactory 基于 lease.path 创建子运行依赖；在 finally 中调用 finalize；将 WorktreeExitReport 写入 AgentTaskResult。

### src/okcode/agents/manager.py

职责：后台任务生命周期。  
改动：快照和查询结果展示 isolation 与 worktree 状态；取消或超时时仍触发 runner 的 finalize。

### src/okcode/agents/notifications.py

职责：子 Agent 完成通知。  
改动：通知摘要增加 worktree path、branch、cleanup decision 和 protection reasons 的短文本。

### src/okcode/tools/defaults.py

职责：工作区绑定工具构建。  
改动：提供可复用的本地工具注册工厂，允许 child runtime 为 worktree 创建新的文件、搜索、命令工具实例，同时保留共享 MCP 工具。

### src/okcode/cli.py

职责：顶层资源组装和生命周期关闭。  
改动：创建 WorktreeManager 和 WorktreeCleanupWorker；把 WorktreeManager、runtime factory 注入 AgentRunner；关闭时停止 cleanup worker。

## 模块交互

### CLI 启动

1. CLI 创建主 Workspace。
2. CLI 创建 WorktreeManager，managed_root 为 <workspace>/.okcode/worktrees。
3. CLI 创建 WorktreeCleanupWorker，并在 Runner 生命周期内启动。
4. CLI 创建 AgentRunner 时注入 WorktreeManager 和 WorktreeRuntimeFactory。

### 角色加载

1. AgentRoleCatalog 扫描角色 Markdown。
2. roles.py 解析 isolation 字段。
3. 未声明 isolation 的角色保持 shared。
4. 非法 isolation 直接 ConfigError，启动失败并提示具体文件和字段。

### 定义式子 Agent worktree 启动

1. 主 Agent 调用 agent 工具，kind=defined。
2. AgentTool 解析 AgentToolRequest。
3. AgentLauncher 找到角色，合并 isolation 策略。
4. isolation=worktree 时生成 WorktreePrepareRequest。
5. AgentRunner 调用 WorktreeManager.prepare。
6. WorktreeManager 创建或恢复 worktree，并初始化环境。
7. AgentRunner 用 lease.path 构造 child runtime。
8. 子 Agent 的文件、搜索、命令、指令、记忆和缓存都绑定 lease.path。
9. 子 Agent 结束后 AgentRunner 调用 finalize。
10. 结果返回主 Agent 或进入后台通知。

### Fork 式子 Agent worktree 启动

1. Fork 请求默认仍强制后台。
2. 如果请求 isolation=worktree，Launcher 生成 fork 专用 worktree identity。
3. Fork 历史仍来自父对话快照，但工具运行根变为 worktree。
4. Worktree 从父仓库当前 HEAD 创建，不自动复制主工作区未提交修改。
5. 未提交修改同步属于后续跨目录同步范围，本阶段不做。

### 快速恢复

1. Manager 发现目标目录已存在。
2. 只读取文件系统和 .okcode/worktree.json。
3. 校验 managed_root、worktree_path、repo_root、repo_common_dir、task_id、branch 和 name。
4. 校验通过则复用目录并执行幂等初始化。
5. 校验失败直接拒绝，不调用 git，不覆盖目录。

### 删除与清理

1. finalize 或显式 delete 请求调用状态检查。
2. Manager 收集 status、untracked、ahead/base_head/upstream 信息。
3. 存在保护原因则保留 worktree。
4. 没有保护原因才调用 git worktree remove。
5. CleanupWorker 扫描时先做三层过滤：路径在 managed_root、元数据存在且匹配、Git worktree 列表匹配。
6. 三层过滤通过后再走同一删除保护。

## 文件组织

    src/okcode/
    ├── worktrees/
    │   ├── __init__.py                 — 导出 WorktreeManager、核心模型
    │   ├── models.py                   — worktree 数据结构和枚举
    │   ├── naming.py                   — 安全名称与分支名派生
    │   ├── git.py                      — Git worktree 命令封装
    │   ├── metadata.py                 — .okcode/worktree.json 读写校验
    │   ├── initializer.py              — 配置复制、hooks、依赖链接、运行文件补齐
    │   ├── manager.py                  — 创建、恢复、退出、删除、清理入口
    │   └── cleanup.py                  — 后台过期清理 Worker
    ├── agents/
    │   ├── models.py                   — 增加 AgentIsolationMode 和 worktree 字段
    │   ├── roles.py                    — 解析 isolation frontmatter
    │   ├── launcher.py                 — 构造 WorktreePrepareRequest
    │   ├── runner.py                   — prepare/finalize worktree 并创建子运行时
    │   ├── manager.py                  — 后台任务快照携带 worktree 状态
    │   ├── notifications.py            — 完成通知携带 worktree 摘要
    │   └── runtime.py                  — 子 Agent 工作区运行依赖工厂
    ├── tools/
    │   └── defaults.py                 — 提供按 Workspace 重建本地工具的工厂
    └── cli.py                          — 组装 WorktreeManager 和 CleanupWorker

    tests/
    ├── unit/
    │   ├── test_worktrees_naming.py
    │   ├── test_worktrees_metadata.py
    │   ├── test_worktrees_manager.py
    │   ├── test_worktrees_initializer.py
    │   ├── test_worktrees_cleanup.py
    │   ├── test_agents_roles.py        — 增加 isolation frontmatter 用例
    │   ├── test_agents_launcher.py     — 增加 isolation 合并与 worktree 请求用例
    │   └── test_agents_runner.py       — 增加 worktree prepare/finalize 用例
    └── integration/
        └── test_subagent_worktree.py   — 临时 Git 仓库端到端隔离测试

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 隔离触发 | 角色 isolation 默认 shared，声明 worktree 才隔离；工具请求可显式升级 | 保持现有子 Agent 兼容，不让所有任务都付出 worktree 成本 |
| 管理目录 | <repo>/.okcode/worktrees/<name> | 当前 .gitignore 已忽略 .okcode/*，目录在仓库内但不被追踪 |
| 元数据位置 | 每个 worktree 内 .okcode/worktree.json | 目录自描述，进程中断后可恢复和安全清理 |
| 快速恢复 | 目录存在时只读文件系统和元数据，不调用 git 创建命令 | 满足快速恢复要求，避免误覆盖用户目录 |
| 分支策略 | 每个 worktree 一个 okcode/agents/<safe-name> 分支 | 让子 Agent 修改天然落在独立分支，后续合并交给上层 Git 流程 |
| 基准状态 | 从主仓库当前 HEAD 创建，不复制主工作区未提交修改 | 保证隔离可预测；跨目录同步明确不在本阶段 |
| 工具 cwd | 不使用 chdir；通过 Workspace(worktree_path) 重建工具 | 与现有工具边界一致，也让缓存天然按绝对路径隔离 |
| 运行依赖 | 子 Agent 路径相关依赖重建，Provider/MCP 等无状态基础设施共享 | 兼顾隔离和性能，不复制无必要对象 |
| 删除保护 | 状态不确定或有变更风险时保留并报告 | 优先保护子 Agent 产物，避免后台清理误删 |
| Windows 链接 | 优先目录连接或符号链接，失败时 warning 降级 | 符合当前 Windows 开发环境，避免静默半初始化 |
| 清理安全 | managed path、metadata、Git worktree 三层过滤 | 删除动作必须先证明目标属于 OkCode 管理范围 |

## Spec 覆盖关系

| Spec 项 | Plan 归属 |
|---------|-----------|
| F1 | AgentIsolationMode、roles.py、AgentToolRequest、AgentLauncher |
| F2、F3 | WorktreeManager、GitWorktreeClient、managed_root |
| F4 | WorktreeNameValidator |
| F5 | WorktreeMetadata、WorktreeManager 快速恢复 |
| F6、F7 | WorktreeRuntimeFactory、Workspace 绑定工具、绝对路径缓存策略 |
| F8-F12 | WorktreeInitializer |
| F13 | WorktreeLease.prompt_note、AgentRunner 上下文注入 |
| F14-F17 | WorktreeManager.finalize/delete、WorktreeExitReport |
| F18 | WorktreeCleanupWorker、cleanup_expired |
| F19 | AgentTaskResult、AgentTaskSnapshot、notifications.py |
| F20 | WorktreeManager 错误转换、AgentRunner 失败隔离 |
| N1 | 默认 shared 与兼容测试 |
| N2、N6 | managed_root 校验、三层过滤、无全局配置修改 |
| N3 | WorktreeInitializer Windows 降级 |
| N4 | WorktreeMetadata 持久化 |
| N5 | 临时 Git 仓库、假 Git client、假 initializer 测试 |
| N7 | worktree 复用、链接大型依赖、幂等初始化 |
