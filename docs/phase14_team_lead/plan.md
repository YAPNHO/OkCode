# OkCode 第十四阶段：Team Lead 长期团队协作 Plan

## 架构概览

本阶段新增 teams 子系统，位于现有 agents 与 worktrees 子系统之上。agents 继续负责一次性子 Agent 启动、运行、工具过滤和后台结果；worktrees 继续负责 Git worktree 创建、恢复、清理和保护；teams 负责长期小组、成员花名册、共享任务、邮箱通信、成员恢复、coordinator 模式和多成员代码合并。

整体采用“团队状态落盘 + 成员后端可插拔 + 工具按上下文注入”的结构：

- TeamStore 负责把小组元数据、成员花名册、名称注册表和共享任务列表写到用户目录下的团队目录，所有并发写入都通过文件锁和原子替换完成。
- MailboxStore 负责每个成员邮箱文件的追加、读取、标记已读和锁管理，消息落盘时统一补时间戳、默认未读状态和摘要。
- TeamRuntime 是团队编排入口，提供创建小组、添加成员、派发任务、发送消息、广播、读取邮箱、恢复成员、终止成员、查询状态和发起合并。
- BackendSelector 按显式请求和环境能力选择成员后端。TerminalPaneBackend 运行完整 OkCode worker 实例；CoroutineBackend 在当前进程中运行轻量成员。强隔离请求失败时直接报错，不退回协程。
- TeamTools 把团队能力暴露给 Team Lead 和团队成员。普通主入口默认不注册团队工具；只有当前会话进入 Team Lead 或 Team Member 上下文后，工具注册表才注入团队工具。
- ApprovalGate 负责需要审批的成员。成员在未获批准前只拿到读类工具和团队消息工具，不能看到普通写文件工具或高风险工具。
- CoordinatorPolicy 读取配置与环境变量两把锁。两把锁都打开时，当前发起方进入 coordinator 模式，工具列表被过滤为读类工具、受控 shell、团队工具和合并工具。
- TeamMergeManager 基于 GitWorktreeClient 和 WorktreeManager 合并成员 worktree 或分支结果。它先做合并前快照，合并失败或冲突不可自动处理时回滚并保留成员产出引用。
- TeamNotificationBridge 把未读团队消息、成员空闲通知、审批请求和合并结果注入下一次 Lead 请求的 SystemInstruction，不伪造成用户消息，也不注入成员完整历史。

用户进入团队工作有两个入口：本地命令 /team create 或 /team use 把当前会话切到 Team Lead 上下文；之后模型才能看到团队工具并创建成员、拆任务和发消息。这样满足“普通主入口看不到团队工具”，也给主 Agent 一个明确升级为 Lead 的边界。

## 核心数据结构

### TeamFeatureConfig

位置：src/okcode/models.py

字段：

- coordinator_enabled: bool，配置层的 coordinator 能力开关，默认 False。
- teams_root: Path | None，可选团队持久化根目录；未配置时使用当前项目目录下 .okcode/team。
- terminal_backend_priority: tuple[str, ...]，自动选择独立终端后端时的候选顺序，默认 windows_terminal、tmux。
- mailbox_lock_timeout_seconds: float，等待邮箱锁的上限。
- mailbox_stale_lock_seconds: float，锁超过该时长视为陈旧锁。

AppConfig 新增 team: TeamFeatureConfig 字段。config.py 的根字段白名单新增 team；旧配置不写 team 时保持当前行为。

### TeamMetadata

位置：src/okcode/teams/models.py

字段：

- version: int，团队状态格式版本。
- name: str，小组安全名称。
- leader_session_id: str，负责人会话标识。
- root_path: Path，小组持久化目录。
- status: TeamStatus，active、archived、failed。
- created_at: datetime。
- updated_at: datetime。

### TeamMember

位置：src/okcode/teams/models.py

字段：

- name: str，小组内唯一成员名。
- role: str，角色说明或已有 Agent role 名称。
- workdir: Path，成员实际工作目录；代码任务通常指向受管理 worktree。
- backend: TeamBackendKind，terminal_pane 或 coroutine。
- backend_handle: TeamBackendHandle | None，后端恢复和唤醒所需信息。
- approval_required: bool。
- status: TeamMemberStatus，idle、running、waiting_approval、blocked、completed、failed、terminated、unrecoverable。
- context_ref: MemberContextRef | None，最近一次可恢复上下文引用。
- mailbox_path: Path。
- last_active_at: datetime | None。
- last_error: str | None。

### TeamBackendKind 与 BackendPreference

位置：src/okcode/teams/models.py

TeamBackendKind：

- terminal_pane：独立终端窗格完整实例。
- coroutine：同进程协程成员。

BackendPreference：

- required_kind: TeamBackendKind | None，显式要求某一种后端。
- require_strong_isolation: bool，要求强隔离时只能选择 terminal_pane。
- allow_auto: bool，是否允许自动选择。

BackendSelector 接收 BackendPreference 和环境能力，返回 BackendSelection。若要求无法满足，抛出 TeamBackendUnavailable，错误中包含缺失能力、候选后端和是否允许降级。

### TeamTask

位置：src/okcode/teams/models.py

字段：

- task_id: str，稳定标识。
- title: str。
- body: str。
- owner: str | None。
- status: TeamTaskStatus，todo、ready、running、blocked、review、done、failed、cancelled。
- dependencies: tuple[str, ...]。
- blocked_reason: str | None。
- output_summary: str | None。
- related_messages: tuple[str, ...]。
- created_at: datetime。
- updated_at: datetime。

任务依赖只做存储、查询和 Lead 决策辅助，不在本阶段做复杂自动排程。

### TeamMessage

位置：src/okcode/teams/models.py

字段：

- message_id: str。
- sender: str。
- recipient: str。
- protocol: TeamMessageProtocol。
- body: str。
- summary: str。
- task_id: str | None。
- created_at: datetime。
- read: bool，默认 False。
- payload: dict[str, JSONValue]，结构化协议消息的扩展字段。

TeamMessageProtocol：

- text。
- task_assignment。
- task_status。
- approval_request。
- approval_decision。
- blocked。
- completion。
- resume。
- broadcast。

### ApprovalRequest 与 ApprovalDecision

位置：src/okcode/teams/models.py

ApprovalRequest 字段：

- request_id: str。
- member_name: str。
- task_id: str。
- plan: str。
- risk_summary: str。
- requested_at: datetime。

ApprovalDecision 字段：

- request_id: str。
- approved: bool。
- reason: str。
- constraints: tuple[str, ...]。
- decided_at: datetime。

ApprovalDecision 必须通过 TeamMessageProtocol.approval_decision 进入成员邮箱。成员运行器只接受匹配 request_id 的批准结果。

### MemberContextRef

位置：src/okcode/teams/models.py

字段：

- session_id: str。
- journal_path: Path。
- last_message_id: str | None。
- workspace_root: Path。
- backend_kind: TeamBackendKind。

成员自然停下后写回该引用。恢复时 TeamRuntime 根据它加载成员历史和工作目录。

### TeamToolContext

位置：src/okcode/teams/models.py

字段：

- team_name: str。
- actor_name: str，lead 或成员名。
- actor_kind: TeamActorKind，lead 或 member。
- coordinator: bool。
- allowed_team_actions: tuple[str, ...]。

该上下文由 ConversationSession 或 TeamWorkerApp 持有，TeamTools 调用时必须存在；不存在则工具不会注册。

### TeamMergeRequest 与 TeamMergeReport

位置：src/okcode/teams/models.py

TeamMergeRequest 字段：

- team_name: str。
- member_names: tuple[str, ...]。
- target_workspace: Path。
- strategy: TeamMergeStrategy，默认 sequential。

TeamMergeReport 字段：

- status: clean、auto_resolved、rolled_back、failed。
- merged_members: tuple[str, ...]。
- skipped_members: tuple[str, ...]。
- conflict_files: tuple[str, ...]。
- rollback_performed: bool。
- message: str。
- source_refs: tuple[str, ...]。

## 核心接口

### TeamRuntime

位置：src/okcode/teams/runtime.py

职责：团队功能的统一编排入口。

接口：

- create_team(name, leader_session_id) -> TeamSnapshot
- use_team(name, leader_session_id) -> TeamSnapshot
- add_member(team_name, request) -> TeamMember
- send_message(team_name, sender, recipient, message) -> MessageDeliveryReport
- broadcast(team_name, sender, message) -> BroadcastReport
- create_task(team_name, request) -> TeamTask
- update_task(team_name, task_id, patch) -> TeamTask
- list_tasks(team_name, filter) -> tuple[TeamTask, ...]
- wake_member(team_name, member_name) -> WakeReport
- terminate_member(team_name, member_name) -> TerminateReport
- restore_member(team_name, member_name) -> RestoreReport
- snapshot(team_name) -> TeamSnapshot
- merge(team_name, request) -> TeamMergeReport

### TeamStore

位置：src/okcode/teams/store.py

职责：小组元数据、成员花名册、名称注册表和共享任务列表的持久化。

接口：

- create(metadata) -> TeamSnapshot
- load(team_name) -> TeamSnapshot
- save_metadata(metadata) -> None
- upsert_member(team_name, member) -> TeamMember
- update_member_status(team_name, member_name, status, context_ref, error) -> TeamMember
- read_registry(team_name) -> NameRegistry
- update_registry(team_name, entry) -> NameRegistry
- list_tasks(team_name) -> tuple[TeamTask, ...]
- mutate_tasks(team_name, mutator) -> tuple[TeamTask, ...]

mutate_tasks 在文件锁内读取最新任务列表、执行变更函数、原子写回，避免并发覆盖。

### MailboxStore

位置：src/okcode/teams/mailbox.py

职责：邮箱文件协议和并发安全。

接口：

- append(mailbox_path, message) -> TeamMessage
- append_many(targets, message_factory) -> BroadcastReport
- unread(mailbox_path) -> tuple[TeamMessage, ...]
- mark_read(mailbox_path, message_ids) -> tuple[TeamMessage, ...]

append 会自动补 created_at、read=False 和 summary。锁路径使用 mailbox_path 加 .lock 后缀。

### FileLock

位置：src/okcode/teams/locking.py

职责：跨进程文件锁。

接口：

- acquire(lock_path, timeout_seconds, stale_seconds) -> FileLockLease
- release(lease) -> None

实现策略是独占创建锁文件；拿不到锁时轮询重试；锁文件年龄超过 stale_seconds 时尝试接管。锁文件内容包含 pid、created_at 和 owner，便于诊断。

### TeamBackend 与 TerminalPaneController

位置：src/okcode/teams/backends.py

TeamBackend 接口：

- kind -> TeamBackendKind
- available() -> BackendCapability
- spawn(member, context) -> TeamBackendHandle
- wake(handle, message_id) -> WakeReport
- terminate(handle) -> TerminateReport
- restore(member) -> RestoreReport

TerminalPaneController 接口：

- available() -> BackendCapability
- spawn(command, cwd, title) -> PaneHandle
- wake(handle) -> WakeReport
- terminate(handle) -> TerminateReport

WindowsTerminalPaneController 优先检测 wt.exe；TmuxPaneController 检测 TMUX 或 tmux 可执行文件。自动选择顺序由 TeamFeatureConfig.terminal_backend_priority 决定。

### CoordinatorPolicy

位置：src/okcode/teams/coordinator.py

接口：

- is_enabled(config, environ) -> bool
- filter_tool_names(registry, team_tools) -> tuple[str, ...]
- build_instruction() -> SystemInstruction

环境变量固定为 OKCODE_COORDINATOR=1。配置 team.coordinator_enabled 和环境变量同时满足才返回 True。

filter_tool_names 保留 read_only 工具、run_command、团队工具和合并工具。run_command 在 coordinator 模式下由 CoordinatorCommandGuard 包装，拒绝明显写文件的 shell 形态，例如重定向写入、Set-Content、Out-File、Remove-Item、Move-Item、Copy-Item 覆盖、直接调用脚本写业务文件等；Git 合并相关命令和只读检查命令允许进入权限系统继续判断。

### TeamToolSuite

位置：src/okcode/teams/tools.py

注册四个模型工具：

- team_task：创建、查询、更新、关闭共享任务。
- team_message：点对点发送、广播、读取未读消息、标记已读。
- team_member：创建成员、唤醒成员、恢复成员、终止成员、查询成员状态。
- team_merge：检查成员变更、执行合并、查看合并报告。

TeamToolSuite 只在 TeamToolContext 存在时注册到当前 registry。Team Lead 拥有全部团队工具；普通成员只拥有 team_task 和 team_message，除非后续明确扩展。

### TeamMergeManager

位置：src/okcode/teams/merge.py

接口：

- inspect_sources(team, members) -> MergeInspection
- merge(request) -> TeamMergeReport
- rollback(snapshot) -> None

合并流程先保存目标仓库 HEAD、当前分支和工作区状态。目标工作区不干净时拒绝合并。每个成员按顺序合并其 branch 或 workdir 变更来源。Git clean merge 视为自动解决；如果出现冲突，只允许已注册的 AutoConflictResolver 处理明确安全的机械冲突；否则执行 git merge --abort 或恢复到合并前 HEAD，并返回 rolled_back。

## 模块设计

### src/okcode/teams/models.py

职责：定义团队、成员、任务、消息、审批、后端、合并、查询快照等纯数据模型。

对外接口：TeamMetadata、TeamMember、TeamTask、TeamMessage、TeamSnapshot、TeamToolContext、TeamMergeReport 等。

依赖：标准库 dataclasses、datetime、enum、pathlib，以及现有 JSONValue 类型。

### src/okcode/teams/naming.py

职责：校验 team_name 和 member_name，防止路径遍历和特殊字符污染邮箱路径、注册表路径和终端标题。

对外接口：validate_team_name、validate_member_name。

依赖：无业务依赖。规则与 worktrees.naming 保持风格一致，但团队名不允许斜杠嵌套，成员名只允许字母、数字、短横线、下划线和点号。

### src/okcode/teams/paths.py

职责：解析用户目录下团队根目录和单个团队目录结构。

对外接口：default_teams_root、TeamPaths。

目录结构：

- 项目目录/.okcode/team/团队名/team.json
- 项目目录/.okcode/team/团队名/members.json
- 项目目录/.okcode/team/团队名/tasks.json
- 项目目录/.okcode/team/团队名/registry.json
- 项目目录/.okcode/team/团队名/mailboxes/成员名.jsonl
- 项目目录/.okcode/team/团队名/member-sessions/成员名/

### src/okcode/teams/locking.py

职责：提供跨进程文件锁，供 Store 和 MailboxStore 使用。

对外接口：FileLock、FileLockLease、LockAcquireError。

依赖：pathlib、datetime、time、os。

### src/okcode/teams/store.py

职责：持久化团队元数据、成员花名册、任务列表和名称注册表。

对外接口：TeamStore。

依赖：locking、paths、models、naming。

### src/okcode/teams/mailbox.py

职责：邮箱文件协议、消息追加、未读查询和已读标记。

对外接口：MailboxStore、MessageDeliveryReport、BroadcastReport。

依赖：locking、models。

### src/okcode/teams/backends.py

职责：成员运行后端抽象、后端能力检测、后端选择、独立终端窗格控制和协程成员控制。

对外接口：BackendSelector、TeamBackend、TerminalPaneBackend、CoroutineBackend、TerminalPaneController。

依赖：agents.launcher、sessions、subprocess、asyncio、models。

### src/okcode/teams/worker.py

职责：独立终端后端启动的完整成员 worker。它读取成员元数据和邮箱，恢复成员上下文，执行未读任务消息，完成或阻塞后写回成员状态并通知 Lead。

对外接口：TeamWorkerApp、run_team_worker。

依赖：ConversationSession、TeamRuntime、MailboxStore、AgentRunner 相关运行时组件。

### src/okcode/teams/runtime.py

职责：团队编排入口，聚合 TeamStore、MailboxStore、BackendSelector、TeamMergeManager、TeamNotificationBridge。

对外接口：TeamRuntime。

依赖：teams 子模块、agents、worktrees。

### src/okcode/teams/tools.py

职责：定义 team_task、team_message、team_member、team_merge 工具，并把工具调用转换为 TeamRuntime 调用。

对外接口：TeamToolSuite、build_team_registry。

依赖：tools.base、tools.models、TeamRuntime、TeamToolContext。

### src/okcode/teams/coordinator.py

职责：coordinator 双锁判断、工具过滤、shell guard 和系统提示注入。

对外接口：CoordinatorPolicy、CoordinatorCommandGuard。

依赖：ToolRegistry、ToolSafety、SystemInstruction。

### src/okcode/teams/merge.py

职责：多成员代码合并、合并前快照、保守自动冲突处理和回滚。

对外接口：TeamMergeManager、AutoConflictResolver。

依赖：worktrees.git、worktrees.manager、teams.models。

### src/okcode/teams/notifications.py

职责：把团队事件转换成 Lead 或成员下一次模型请求可见的 SystemInstruction。

对外接口：TeamNotificationBridge。

依赖：prompt.SystemInstruction、TeamStore、MailboxStore。

### src/okcode/commands/handlers.py 与 defaults.py

职责：新增 /team 本地命令。

命令形态：

- /team：显示当前团队状态或未启用提示。
- /team create 名称：创建团队并把当前会话设为 Team Lead。
- /team use 名称：恢复已有团队并把当前会话设为 Team Lead。
- /team leave：退出 Team Lead 上下文，团队状态仍保留。
- /team status：展示团队、成员、任务和消息摘要。

该命令是本地命令，不经过模型。

### src/okcode/conversation.py

职责：接入 TeamRuntime 和 TeamToolContext。

改动点：

- ConversationSession 增加可选 team_runtime、team_context、coordinator_policy。
- _build_normal_request 注入 TeamNotificationBridge 产出的 SystemInstruction。
- _resolve_skill_tools 后增加团队工具与 coordinator 过滤步骤。
- status_snapshot 增加 coordinator 状态和当前 team_name。

## 模块交互

### 创建团队并进入 Lead 模式

1. 用户输入 /team create core-team。
2. Command handler 调用 TeamRuntime.create_team。
3. TeamStore 在用户目录创建团队目录，写入 team.json、members.json、tasks.json、registry.json 和 mailboxes 目录。
4. ConversationSession 写入 TeamToolContext(actor_kind=lead)。
5. 后续模型请求的工具列表注入 team_task、team_message、team_member、team_merge。

### Lead 创建成员

1. Lead 调用 team_member.create。
2. TeamRuntime 校验成员名和工作目录，调用 BackendSelector。
3. BackendSelector 按请求选择 terminal_pane 或 coroutine；强隔离不可用则失败。
4. TeamStore 写入 TeamMember 和 NameRegistryEntry。
5. 后端 spawn 成员，返回 backend_handle。
6. TeamRuntime 更新成员状态，并返回可诊断结果。

### Lead 拆任务并指派

1. Lead 调用 team_task.create 写入共享任务，dependencies 字段落盘。
2. Lead 调用 team_message.send，protocol=task_assignment。
3. TeamRuntime 通过名称注册表查目标成员邮箱。
4. MailboxStore 在锁内追加消息，自动补时间戳、未读状态和摘要。
5. 如果目标是 terminal_pane，TeamRuntime 调用后端 wake。
6. 发送结果说明 delivered、failed 或 written_but_not_woken。

### 需要审批的成员执行任务

1. 成员 worker 读取未读 task_assignment。
2. 如果 TeamMember.approval_required=True，ApprovalGate 只暴露读类工具和 team_message。
3. 成员生成计划，通过 team_message 向 Lead 发送 approval_request，然后状态变为 waiting_approval。
4. Lead 审阅后发送 approval_decision。
5. 成员恢复时读取匹配 request_id 的批准结果。approved=True 时重新构造完整允许工具；approved=False 时标记 blocked 或 failed 并写入原因。

### 成员自然停下并恢复

1. 成员 runner 自然完成或无事可做时，写回 MemberContextRef 和 idle 状态。
2. TeamNotificationBridge 给 Lead 注入 completion 或 idle 通知。
3. Lead 后续发新消息到该成员邮箱。
4. TeamRuntime.restore_member 根据 MemberContextRef 恢复 ConversationSession 或唤醒终端 worker。
5. 恢复失败时写入 unrecoverable 或 blocked 状态，并返回缺失项。

### coordinator 模式

1. 配置 team.coordinator_enabled=True。
2. 用户进程环境 OKCODE_COORDINATOR=1。
3. ConversationSession 创建请求时 CoordinatorPolicy.is_enabled 返回 True。
4. 工具过滤保留 read_only、run_command、团队工具和合并工具，移除 write_file、edit_file 等普通写入工具。
5. Prompt 中注入 coordinator 职责说明。
6. run_command 经过 CoordinatorCommandGuard，明显写文件的 shell 形态被拒绝。

### 多成员合并

1. Lead 调用 team_merge.merge，指定成员列表。
2. TeamMergeManager 检查目标工作区必须干净。
3. TeamMergeManager 为目标 HEAD 和当前分支建立合并前快照。
4. 依次合并成员分支或 worktree 来源。
5. clean merge 直接记录成功；已注册 resolver 能安全处理时记录 auto_resolved。
6. 不可处理冲突时 abort 或恢复合并前 HEAD，返回 rolled_back 报告。

## 文件组织

新增文件：

- src/okcode/teams/__init__.py：导出团队子系统公共类型。
- src/okcode/teams/models.py：团队、成员、任务、消息、审批、后端、合并模型。
- src/okcode/teams/naming.py：团队名和成员名安全校验。
- src/okcode/teams/paths.py：用户目录团队持久化路径。
- src/okcode/teams/locking.py：锁文件工具。
- src/okcode/teams/store.py：团队元数据、成员、任务和注册表持久化。
- src/okcode/teams/mailbox.py：邮箱读写与消息协议。
- src/okcode/teams/backends.py：后端选择、终端窗格后端、协程后端。
- src/okcode/teams/worker.py：独立成员 worker 入口。
- src/okcode/teams/runtime.py：团队编排入口。
- src/okcode/teams/tools.py：team_task、team_message、team_member、team_merge 工具。
- src/okcode/teams/coordinator.py：coordinator 双锁、工具过滤和 shell guard。
- src/okcode/teams/merge.py：代码合并、冲突处理和回滚。
- src/okcode/teams/notifications.py：团队通知注入。

修改文件：

- src/okcode/models.py：AppConfig 新增 TeamFeatureConfig。
- src/okcode/config.py：解析可选 team 配置块。
- src/okcode/cli.py：增加 team-worker 入口参数。
- src/okcode/app.py：创建 TeamRuntime，处理 /team 后同步 TeamToolContext。
- src/okcode/conversation.py：注入团队工具、团队通知和 coordinator 过滤。
- src/okcode/tools/defaults.py：支持按上下文注入团队工具，保留普通默认 registry 不变。
- src/okcode/commands/defaults.py：注册 /team 命令。
- src/okcode/commands/handlers.py：实现 /team 命令处理。
- src/okcode/models.py 或 terminal.py 相关事件模型：新增团队状态、团队消息和 coordinator 状态渲染事件。
- tests/unit/test_teams_*.py：新增团队持久化、邮箱、锁、后端选择、工具过滤、审批、恢复、合并单元测试。
- tests/integration/test_team_lead_flow.py：新增端到端团队流程测试。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 团队状态位置 | 项目目录 .okcode/team/团队名 | 团队状态与项目绑定，便于项目级备份和迁移。 |
| 团队启用方式 | /team create 或 /team use 后当前会话成为 Lead | 让普通主入口默认看不到团队工具，同时给模型一个明确的 Lead 边界。 |
| 模型工具形态 | 四个聚合工具 team_task、team_message、team_member、team_merge | 工具数量少、职责清晰，参数比单一大工具更可控。 |
| 消息协议 | JSONL 邮箱，每条消息独立一行 | 追加友好，适合锁内写入，便于恢复和排查坏消息。 |
| 并发保护 | 锁文件 + 原子替换 | Windows 友好，不依赖 Unix flock。 |
| 后端降级 | 只有 auto 允许选择弱后端，显式强隔离失败即报错 | 直接满足“不静默降级”。 |
| 终端后端 | Windows Terminal 优先，tmux 作为可选能力 | 项目运行环境以 Windows 为主，同时保留终端复用工具扩展点。 |
| 审批强制 | 未批准前工具层过滤写工具，而不是只靠提示词 | 防止成员绕过审批直接改文件。 |
| coordinator 双锁 | team.coordinator_enabled + OKCODE_COORDINATOR=1 | 配置授权和用户主动启用都明确，避免误触发。 |
| coordinator shell | 保留 run_command 但加 CoordinatorCommandGuard | 兼顾 Git 合并需求和防止 shell 成为写文件后门。 |
| 团队通知 | SystemInstruction 注入，不写成用户消息 | 延续 Phase 12 后台通知设计，避免污染会话角色语义。 |
| 合并策略 | 顺序合并 + 合并前快照 + 保守自动解决 | 能处理干净合并和明确安全冲突，遇到不确定冲突可回滚上报。 |

## Spec 覆盖关系

- F1、F2、F3：TeamMetadata、TeamMember、TeamStore、TeamPaths、naming.py。
- F4、F5：TeamBackend、BackendSelector、TerminalPaneBackend、CoroutineBackend。
- F6：TeamToolContext、TeamToolSuite、ConversationSession 工具注入。
- F7、F8：TeamTask、TeamStore.mutate_tasks、FileLock。
- F9、F10、F11：NameRegistry、TeamMessage、MailboxStore、FileLock。
- F12、F13、F14：TeamRuntime.send_message、broadcast、TerminalPaneBackend.wake。
- F15：ApprovalRequest、ApprovalDecision、ApprovalGate。
- F16：TeamTools 中的 task 与 member 编排能力，Lead 通过工具写共享任务和派发消息。
- F17、F18：TeamMemberStatus、MemberContextRef、TeamRuntime.restore_member。
- F19、F20：TeamMergeManager、TeamMergeRequest、TeamMergeReport。
- F21、F22、F23：TeamFeatureConfig、CoordinatorPolicy、CoordinatorCommandGuard、SystemInstruction。
- F24：/team status、TeamRuntime.snapshot、终端渲染事件。

## 自检

- spec 的 24 条功能需求均有模块归属，没有缺口。
- 新增团队工具默认不进入普通 registry，只有 TeamToolContext 存在时注入，满足工具可见性边界。
- coordinator 模式不改写现有权限系统，而是在工具可见性和 run_command 包装层增加约束，避免影响普通会话。
- 持久化和邮箱都以锁文件为并发边界，符合 Windows 环境要求。
- 合并策略保守，遇到不确定冲突回滚上报，不尝试猜测业务语义。
