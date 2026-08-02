# OkCode 第十四阶段：Team Lead 长期团队协作 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | src/okcode/teams/__init__.py | 导出 teams 子系统公共类型和运行时入口 |
| 新建 | src/okcode/teams/models.py | 团队、成员、任务、消息、审批、后端、合并数据模型 |
| 新建 | src/okcode/teams/naming.py | team_name 和 member_name 安全校验 |
| 新建 | src/okcode/teams/paths.py | 用户目录团队持久化路径结构 |
| 新建 | src/okcode/teams/locking.py | Windows 友好的锁文件机制 |
| 新建 | src/okcode/teams/store.py | 小组元数据、成员、注册表、共享任务持久化 |
| 新建 | src/okcode/teams/mailbox.py | 邮箱 JSONL 协议、追加、读取、标记已读 |
| 新建 | src/okcode/teams/backends.py | 成员后端抽象、后端选择、终端窗格和协程后端 |
| 新建 | src/okcode/teams/runtime.py | TeamRuntime 编排入口 |
| 新建 | src/okcode/teams/tools.py | team_task、team_message、team_member、team_merge 工具 |
| 新建 | src/okcode/teams/coordinator.py | coordinator 双锁、工具过滤和 shell guard |
| 新建 | src/okcode/teams/notifications.py | 团队通知 SystemInstruction 注入 |
| 新建 | src/okcode/teams/worker.py | 独立终端成员 worker 入口 |
| 新建 | src/okcode/teams/merge.py | 多成员代码合并、冲突处理和回滚 |
| 修改 | src/okcode/models.py | 新增 TeamFeatureConfig、团队相关事件模型 |
| 修改 | src/okcode/config.py | 解析可选 team 配置块 |
| 修改 | src/okcode/cli.py | 增加 team worker CLI 入口 |
| 修改 | src/okcode/app.py | 创建 TeamRuntime，接入 /team 上下文切换 |
| 修改 | src/okcode/conversation.py | 注入团队工具、团队通知和 coordinator 过滤 |
| 修改 | src/okcode/tools/defaults.py | 支持按团队上下文注入团队工具 |
| 修改 | src/okcode/commands/defaults.py | 注册 /team 本地命令 |
| 修改 | src/okcode/commands/handlers.py | 实现 /team 命令处理 |
| 修改 | src/okcode/terminal.py | 渲染团队状态、团队消息和 coordinator 状态 |
| 新建 | tests/unit/test_teams_models.py | 团队模型、名称校验、路径规则测试 |
| 新建 | tests/unit/test_teams_store.py | TeamStore 持久化与并发任务更新测试 |
| 新建 | tests/unit/test_teams_mailbox.py | 邮箱协议、锁、广播和已读测试 |
| 新建 | tests/unit/test_teams_backends.py | 后端选择、降级失败、唤醒报告测试 |
| 新建 | tests/unit/test_teams_runtime.py | TeamRuntime 创建、成员、任务、消息、恢复测试 |
| 新建 | tests/unit/test_teams_tools.py | 团队工具 schema、可见性和调用行为测试 |
| 新建 | tests/unit/test_teams_coordinator.py | coordinator 双锁、工具过滤、shell guard 测试 |
| 新建 | tests/unit/test_teams_merge.py | 合并成功、冲突回滚和来源引用测试 |
| 新建 | tests/integration/test_team_lead_flow.py | 端到端 Team Lead 流程测试 |

## T1: 建立 teams 包和基础导出

**文件：** src/okcode/teams/__init__.py
**依赖：** 无
**步骤：**
1. 新建 teams 包目录和 __init__.py。
2. 在 __init__.py 中只导出后续稳定公共类型名，先保留从 models、runtime 延迟可用的导入结构。
3. 确保导入 okcode.teams 不触发文件系统、终端或 Git 副作用。

**验证：** 运行 uv run python -c "import okcode.teams; print('ok')"，输出 ok。

## T2: 定义团队核心模型

**文件：** src/okcode/teams/models.py
**依赖：** T1
**步骤：**
1. 定义 TeamStatus、TeamMemberStatus、TeamTaskStatus、TeamBackendKind、TeamMessageProtocol、TeamActorKind、TeamMergeStrategy 枚举。
2. 定义 TeamMetadata、TeamBackendHandle、MemberContextRef、TeamMember、TeamTask、TeamMessage、ApprovalRequest、ApprovalDecision。
3. 定义 TeamToolContext、TeamSnapshot、NameRegistryEntry、NameRegistry、MessageDeliveryReport、BroadcastReport。
4. 定义 TeamMergeRequest、TeamMergeReport、BackendPreference、BackendSelection、BackendCapability。
5. 字段名和 plan.md 保持一致，所有路径字段使用 Path，时间字段使用 datetime。

**验证：** 运行 uv run python -c "from okcode.teams.models import TeamMetadata, TeamMessage; print(TeamMetadata.__name__, TeamMessage.__name__)"，输出两个类名。

## T3: 实现名称校验

**文件：** src/okcode/teams/naming.py、tests/unit/test_teams_models.py
**依赖：** T2
**步骤：**
1. 实现 validate_team_name(name: str) -> str，只允许字母、数字、短横线、下划线和点号，不允许空值、单点、双点、斜杠、反斜杠、冒号和控制字符。
2. 实现 validate_member_name(name: str) -> str，规则与 team 名相同。
3. 为合法名称、空字符串、路径遍历、Windows 盘符、反斜杠、控制字符补单元测试。

**验证：** 运行 uv run pytest tests/unit/test_teams_models.py -q，通过名称校验用例。

## T4: 实现团队路径结构

**文件：** src/okcode/teams/paths.py、tests/unit/test_teams_models.py
**依赖：** T3
**步骤：**
1. 实现 default_teams_root()，默认返回当前项目目录下 .okcode/team。
2. 定义 TeamPaths，包含 root、team_json、members_json、tasks_json、registry_json、mailboxes_dir、member_sessions_dir。
3. 实现 TeamPaths.for_team(teams_root, team_name)，内部调用 validate_team_name，并确保最终路径位于 teams_root 内。
4. 增加测试验证路径都落在临时 teams root 下。

**验证：** 运行 uv run pytest tests/unit/test_teams_models.py -q，通过路径用例。

## T5: 实现锁文件机制

**文件：** src/okcode/teams/locking.py、tests/unit/test_teams_mailbox.py
**依赖：** T4
**步骤：**
1. 定义 FileLockLease 和 LockAcquireError。
2. 实现 FileLock.acquire(lock_path, timeout_seconds, stale_seconds, owner)，通过独占创建锁文件拿锁。
3. 锁文件内容写入 pid、owner、created_at。
4. 拿不到锁时按短间隔重试；超过 timeout 抛出 LockAcquireError。
5. 锁文件年龄超过 stale_seconds 时尝试接管，并记录接管状态。
6. 实现 FileLock.release(lease)，只删除当前 lease 拥有的锁文件。
7. 增加测试覆盖成功拿锁、超时失败、陈旧锁接管、释放锁。

**验证：** 运行 uv run pytest tests/unit/test_teams_mailbox.py -q，通过锁相关用例。

## T6: 实现 TeamStore 创建和加载

**文件：** src/okcode/teams/store.py、tests/unit/test_teams_store.py
**依赖：** T2、T3、T4、T5
**步骤：**
1. 定义 TeamStore，构造参数包含 teams_root、锁超时和陈旧锁阈值。
2. 实现 create(metadata)，创建团队目录、mailboxes、member-sessions，写入 team.json、空 members.json、空 tasks.json、空 registry.json。
3. 实现 load(team_name)，读取并返回 TeamSnapshot。
4. JSON 写入使用临时文件加原子替换。
5. 增加测试验证创建后的目录和文件齐全，重新实例化 TeamStore 后能加载同一团队。

**验证：** 运行 uv run pytest tests/unit/test_teams_store.py -q，通过创建和加载用例。

## T7: 实现成员花名册和名称注册表持久化

**文件：** src/okcode/teams/store.py、tests/unit/test_teams_store.py
**依赖：** T6
**步骤：**
1. 实现 upsert_member(team_name, member)，在锁内更新 members.json。
2. 实现 update_member_status(team_name, member_name, status, context_ref, error)。
3. 实现 read_registry(team_name) 和 update_registry(team_name, entry)。
4. 成员 mailbox_path 必须由 TeamPaths 和 member_name 派生，不能由外部直接传入任意路径。
5. 增加测试验证成员名唯一、状态更新保留其他字段、注册表解析目标邮箱。

**验证：** 运行 uv run pytest tests/unit/test_teams_store.py -q，通过成员和注册表用例。

## T8: 实现共享任务列表持久化

**文件：** src/okcode/teams/store.py、tests/unit/test_teams_store.py
**依赖：** T6
**步骤：**
1. 实现 list_tasks(team_name)。
2. 实现 mutate_tasks(team_name, mutator)，在锁内读取最新任务、执行 mutator、原子写回。
3. 实现任务 id 查重、依赖字段原样保留、更新时间刷新。
4. 增加测试模拟两次连续 mutation 更新不同任务，确认不会丢字段。

**验证：** 运行 uv run pytest tests/unit/test_teams_store.py -q，通过任务持久化用例。

## T9: 实现邮箱协议

**文件：** src/okcode/teams/mailbox.py、tests/unit/test_teams_mailbox.py
**依赖：** T2、T5
**步骤：**
1. 定义 MailboxStore。
2. 实现 append(mailbox_path, message)，在锁内追加 JSONL。
3. append 自动补 message_id、created_at、read=False 和空缺摘要。
4. 实现 unread(mailbox_path)，只返回未读消息。
5. 实现 mark_read(mailbox_path, message_ids)，通过锁内重写 JSONL 标记已读。
6. 读取遇到坏行时返回可诊断错误，不静默吞掉。
7. 增加测试覆盖自动时间戳、默认未读、标记已读、坏 JSON 行报错。

**验证：** 运行 uv run pytest tests/unit/test_teams_mailbox.py -q，通过邮箱协议用例。

## T10: 实现广播写入报告

**文件：** src/okcode/teams/mailbox.py、tests/unit/test_teams_mailbox.py
**依赖：** T9
**步骤：**
1. 实现 append_many(targets, message_factory)。
2. 每个目标独立调用 append，收集成功和失败。
3. 任一目标失败时不得回滚其他目标已写入消息。
4. 增加测试让一个目标邮箱路径不可写，确认其他目标成功且报告中包含失败项。

**验证：** 运行 uv run pytest tests/unit/test_teams_mailbox.py -q，通过广播用例。

## T11: 增加 team 配置解析

**文件：** src/okcode/models.py、src/okcode/config.py、tests/unit/test_config.py
**依赖：** T2
**步骤：**
1. 在 models.py 定义 TeamFeatureConfig，字段与 plan.md 一致，并设置兼容默认值。
2. AppConfig 增加 team: TeamFeatureConfig 字段。
3. config.py 根字段白名单加入 team。
4. 解析 team.coordinator_enabled、team.teams_root、team.terminal_backend_priority、team.mailbox_lock_timeout_seconds、team.mailbox_stale_lock_seconds。
5. 未配置 team 时保持旧配置文件可加载。
6. 增加测试覆盖默认值、合法配置、未知字段、非法布尔值、非法数字。

**验证：** 运行 uv run pytest tests/unit/test_config.py -q，通过配置用例。

## T12: 实现后端选择器

**文件：** src/okcode/teams/backends.py、tests/unit/test_teams_backends.py
**依赖：** T2、T11
**步骤：**
1. 定义 TeamBackend 抽象接口和 TeamBackendUnavailable。
2. 实现 BackendSelector.select(preference, backends)。
3. 显式 required_kind 不可用时抛出错误，错误中包含缺失后端和候选能力。
4. require_strong_isolation=True 时只能选择 terminal_pane。
5. allow_auto=True 时按配置优先级选择可用后端。
6. 增加假后端测试强隔离成功、强隔离失败、自动选择、禁止自动选择。

**验证：** 运行 uv run pytest tests/unit/test_teams_backends.py -q，通过后端选择用例。

## T13: 实现终端窗格后端能力检测和唤醒报告

**文件：** src/okcode/teams/backends.py、tests/unit/test_teams_backends.py
**依赖：** T12
**步骤：**
1. 定义 TerminalPaneController 接口。
2. 实现 WindowsTerminalPaneController.available()，检测 wt.exe 是否可用。
3. 实现 TmuxPaneController.available()，检测 tmux 或 TMUX 环境。
4. 实现 TerminalPaneBackend.spawn、wake、terminate 的可测试包装，真实执行调用隔离在 controller。
5. 唤醒失败时返回 written_but_not_woken 风格报告，不影响已落盘消息。
6. 增加 fake controller 测试 spawn、wake 成功、wake 失败、terminate。

**验证：** 运行 uv run pytest tests/unit/test_teams_backends.py -q，通过终端后端用例。

## T14: 实现协程后端骨架

**文件：** src/okcode/teams/backends.py、tests/unit/test_teams_backends.py
**依赖：** T12
**步骤：**
1. 实现 CoroutineBackend，kind 为 coroutine。
2. spawn 创建可恢复的 backend_handle，但不直接混用 Lead 的上下文。
3. wake 将成员加入当前进程待运行队列或返回可测试 wake 报告。
4. terminate 标记待运行任务取消。
5. 增加测试验证协程后端可用、handle 稳定、wake 报告正确。

**验证：** 运行 uv run pytest tests/unit/test_teams_backends.py -q，通过协程后端用例。

## T15: 实现 TeamRuntime 创建和查询

**文件：** src/okcode/teams/runtime.py、tests/unit/test_teams_runtime.py
**依赖：** T6、T7、T8、T9、T12
**步骤：**
1. 定义 TeamRuntime 构造函数，接收 TeamStore、MailboxStore、BackendSelector、TeamMergeManager 可选依赖。
2. 实现 create_team(name, leader_session_id)。
3. 实现 use_team(name, leader_session_id)。
4. 实现 snapshot(team_name)，返回成员、任务、未读消息数量和可恢复性摘要。
5. 增加测试验证创建、恢复、查询空团队状态。

**验证：** 运行 uv run pytest tests/unit/test_teams_runtime.py -q，通过 TeamRuntime 创建查询用例。

## T16: 实现成员创建、唤醒和终止编排

**文件：** src/okcode/teams/runtime.py、tests/unit/test_teams_runtime.py
**依赖：** T7、T12、T13、T14、T15
**步骤：**
1. 实现 add_member(team_name, request)，校验成员名、选择后端、派生邮箱路径、写花名册和注册表。
2. 实现 wake_member(team_name, member_name)，从注册表读取 backend_handle 后调用后端 wake。
3. 实现 terminate_member(team_name, member_name)，调用后端 terminate 并更新成员状态。
4. 后端选择失败时不写入半成品成员。
5. 增加测试覆盖成员创建成功、强隔离不可用失败、唤醒失败状态、终止状态。

**验证：** 运行 uv run pytest tests/unit/test_teams_runtime.py -q，通过成员编排用例。

## T17: 实现团队任务 runtime 方法

**文件：** src/okcode/teams/runtime.py、tests/unit/test_teams_runtime.py
**依赖：** T8、T15
**步骤：**
1. 实现 create_task(team_name, request)，生成稳定 task_id 并写入 dependencies。
2. 实现 update_task(team_name, task_id, patch)。
3. 实现 list_tasks(team_name, filter)。
4. 更新任务时刷新 updated_at，并保留未 patch 的字段。
5. 增加测试覆盖创建任务、依赖字段、状态更新、找不到任务报错。

**验证：** 运行 uv run pytest tests/unit/test_teams_runtime.py -q，通过任务 runtime 用例。

## T18: 实现消息发送、广播和终端唤醒编排

**文件：** src/okcode/teams/runtime.py、tests/unit/test_teams_runtime.py
**依赖：** T9、T10、T13、T16
**步骤：**
1. 实现 send_message(team_name, sender, recipient, message)。
2. 发送前必须通过注册表解析 recipient，找不到目标返回结构化失败。
3. 消息成功落盘后，如果目标后端是 terminal_pane，调用 wake_member。
4. wake 失败时返回已落盘但未唤醒报告。
5. 实现 broadcast(team_name, sender, message)，排除 sender 自己时行为明确。
6. 增加测试覆盖点对点成功、目标不存在、终端唤醒失败、广播部分失败。

**验证：** 运行 uv run pytest tests/unit/test_teams_runtime.py -q，通过消息编排用例。

## T19: 实现审批协议辅助

**文件：** src/okcode/teams/runtime.py、src/okcode/teams/models.py、tests/unit/test_teams_runtime.py
**依赖：** T18
**步骤：**
1. 增加构造 approval_request 消息的辅助函数或 runtime 方法。
2. 增加构造 approval_decision 消息的辅助函数或 runtime 方法。
3. 成员需要审批时，runtime 能把成员状态设为 waiting_approval。
4. 驳回时将成员状态设为 blocked 并记录原因。
5. 增加测试覆盖审批请求、批准、驳回、request_id 不匹配。

**验证：** 运行 uv run pytest tests/unit/test_teams_runtime.py -q，通过审批协议用例。

## T20: 实现成员恢复入口

**文件：** src/okcode/teams/runtime.py、tests/unit/test_teams_runtime.py
**依赖：** T7、T16
**步骤：**
1. 实现 restore_member(team_name, member_name)。
2. 检查 MemberContextRef、工作目录、backend_handle 是否存在。
3. 终端后端成员优先唤醒已有窗格；协程后端成员恢复到待运行队列。
4. 缺失上下文、缺失工作目录或后端不可用时返回具体原因，并把成员状态更新为 unrecoverable 或 blocked。
5. 增加测试覆盖恢复成功、缺 context_ref、缺 workdir、后端不可用。

**验证：** 运行 uv run pytest tests/unit/test_teams_runtime.py -q，通过成员恢复用例。

## T21: 实现团队通知桥

**文件：** src/okcode/teams/notifications.py、tests/unit/test_teams_runtime.py
**依赖：** T9、T15、T18
**步骤：**
1. 定义 TeamNotificationBridge。
2. 实现把 Lead 未读消息、成员空闲通知、审批请求、阻塞上报和合并结果转换成 SystemInstruction。
3. 通知内容只包含摘要、任务标识、成员名和消息引用，不注入成员完整历史。
4. 读取后可按策略标记为已读或保留未读，行为在测试中固定。
5. 增加测试确认通知 kind、priority 和内容摘要。

**验证：** 运行 uv run pytest tests/unit/test_teams_runtime.py -q，通过通知桥用例。

## T22: 实现团队工具 schema

**文件：** src/okcode/teams/tools.py、tests/unit/test_teams_tools.py
**依赖：** T15、T16、T17、T18、T20
**步骤：**
1. 定义 TeamToolSuite。
2. 实现 team_task 工具定义，支持 create、list、update、close。
3. 实现 team_message 工具定义，支持 send、broadcast、unread、mark_read。
4. 实现 team_member 工具定义，支持 create、wake、restore、terminate、status。
5. 实现 team_merge 工具定义，支持 inspect、merge。
6. 每个工具参数 schema 明确 action、team_name 和必要字段，错误返回结构化消息。
7. 增加测试验证四个工具定义名称、安全等级、参数 schema 和 timeout。

**验证：** 运行 uv run pytest tests/unit/test_teams_tools.py -q，通过工具 schema 用例。

## T23: 实现团队工具调用行为

**文件：** src/okcode/teams/tools.py、tests/unit/test_teams_tools.py
**依赖：** T22
**步骤：**
1. 将 team_task action 映射到 TeamRuntime 任务方法。
2. 将 team_message action 映射到 TeamRuntime 消息方法和 MailboxStore 读取方法。
3. 将 team_member action 映射到成员创建、唤醒、恢复、终止和 snapshot。
4. 将 team_merge action 映射到 TeamMergeManager。
5. 工具调用时必须检查 TeamToolContext 存在；不存在时返回不可用错误。
6. 增加 fake runtime 测试每个 action 调用正确方法。

**验证：** 运行 uv run pytest tests/unit/test_teams_tools.py -q，通过工具调用用例。

## T24: 接入团队工具可见性

**文件：** src/okcode/tools/defaults.py、src/okcode/conversation.py、tests/unit/test_teams_tools.py
**依赖：** T22、T23
**步骤：**
1. 在 tools/defaults.py 增加按 TeamToolContext 注入团队工具的函数，普通 build_default_registry 不注入。
2. 在 ConversationSession 中保存可选 team_context。
3. 在模型请求构建前，根据 team_context 扩展或过滤工具列表。
4. Team Lead 可见四个团队工具；Team Member 默认只可见 team_task 和 team_message。
5. 普通主入口和普通子 Agent 不设置 team_context，因此看不到团队工具。
6. 增加测试覆盖普通会话、Lead、Member 三种工具列表。

**验证：** 运行 uv run pytest tests/unit/test_teams_tools.py -q，通过工具可见性用例。

## T25: 接入 /team 命令

**文件：** src/okcode/commands/defaults.py、src/okcode/commands/handlers.py、src/okcode/app.py、tests/unit/test_commands_handlers.py
**依赖：** T15、T24
**步骤：**
1. 在默认命令注册表加入 /team 本地命令。
2. 在 handler 中解析 /team、/team create <name>、/team use <name>、/team leave、/team status。
3. create 和 use 调用 TeamRuntime 并把 ConversationSession 切到 Lead 上下文。
4. leave 清空当前 ConversationSession 的 team_context，但不删除团队状态。
5. status 返回团队状态事件。
6. 增加命令 handler 测试覆盖用法错误、创建、恢复、离开和状态查询。

**验证：** 运行 uv run pytest tests/unit/test_commands_handlers.py -q，通过 /team 命令用例。

## T26: 渲染团队状态事件

**文件：** src/okcode/models.py、src/okcode/terminal.py、tests/unit/test_terminal.py
**依赖：** T25
**步骤：**
1. 在事件模型中增加团队状态、团队通知和 coordinator 状态渲染所需类型。
2. 在 terminal.py 增加团队状态表格渲染，显示 team、leader、成员状态、未读数量、任务摘要。
3. 空状态显示明确提示，而不是空表格。
4. 增加测试验证团队状态事件不会破坏现有终端渲染。

**验证：** 运行 uv run pytest tests/unit/test_terminal.py -q，通过团队渲染用例。

## T27: 实现 coordinator 双锁策略

**文件：** src/okcode/teams/coordinator.py、tests/unit/test_teams_coordinator.py
**依赖：** T11、T22
**步骤：**
1. 实现 CoordinatorPolicy.is_enabled(config, environ)，只有 team.coordinator_enabled=True 且 OKCODE_COORDINATOR=1 时返回 True。
2. 实现 build_instruction()，说明 coordinator 职责和不能直接写文件。
3. 实现 filter_tool_names(registry, team_tools)，保留 read_only、run_command、团队工具、合并工具。
4. 增加测试覆盖只开配置、只开环境变量、两把锁都开、两把锁都关。

**验证：** 运行 uv run pytest tests/unit/test_teams_coordinator.py -q，通过双锁用例。

## T28: 实现 coordinator shell guard

**文件：** src/okcode/teams/coordinator.py、tests/unit/test_teams_coordinator.py
**依赖：** T27
**步骤：**
1. 定义 CoordinatorCommandGuard。
2. 允许只读命令和 Git 状态检查命令。
3. 允许 Git 合并相关命令继续进入权限系统。
4. 拒绝明显写文件命令形态，包括重定向写入、Set-Content、Out-File、Remove-Item、Move-Item、覆盖型 Copy-Item、直接调用已知写文件脚本。
5. 返回拒绝时给出说明，避免模型以为命令不存在。
6. 增加测试覆盖允许和拒绝命令样例。

**验证：** 运行 uv run pytest tests/unit/test_teams_coordinator.py -q，通过 shell guard 用例。

## T29: 在 ConversationSession 接入 coordinator

**文件：** src/okcode/conversation.py、src/okcode/tools/defaults.py、tests/unit/test_teams_coordinator.py
**依赖：** T27、T28
**步骤：**
1. ConversationSession 增加可选 coordinator_policy 和 coordinator 状态。
2. 在工具解析阶段应用 coordinator 工具过滤。
3. 对 run_command 工具应用 CoordinatorCommandGuard 包装或预执行检查。
4. 在 prompt 额外 SystemInstruction 中注入 coordinator 说明。
5. status_snapshot 增加 coordinator 是否生效。
6. 增加测试验证 coordinator 生效时写工具不可见、读工具可见、run_command 受 guard 约束。

**验证：** 运行 uv run pytest tests/unit/test_teams_coordinator.py -q，通过 ConversationSession coordinator 用例。

## T30: 实现独立成员 worker 入口

**文件：** src/okcode/teams/worker.py、src/okcode/cli.py、tests/unit/test_teams_backends.py
**依赖：** T13、T15、T18、T20
**步骤：**
1. 在 worker.py 定义 TeamWorkerApp，接收 team_name、member_name、teams_root 和工作目录。
2. worker 启动时加载 TeamRuntime、成员元数据和邮箱未读消息。
3. 暂定处理流程为读取消息、恢复上下文、执行或等待审批、写回成员状态；真实模型循环接入通过后续任务串联 AgentRunner。
4. 在 cli.py 增加内部 team-worker 入口参数，供终端后端 spawn。
5. 测试中用 fake runtime 验证 CLI 参数能路由到 worker。

**验证：** 运行 uv run pytest tests/unit/test_teams_backends.py -q，通过 worker 入口用例。

## T31: 串联成员 worker 与 Agent 运行时

**文件：** src/okcode/teams/worker.py、src/okcode/agents/runtime.py、tests/unit/test_teams_runtime.py
**依赖：** T19、T20、T30
**步骤：**
1. worker 根据成员 role 和消息构造 AgentLaunchRequest 或复用已有 AgentRunner 输入。
2. 审批未通过前只构造读类工具和团队消息工具。
3. 获得批准后构造完整成员可见工具。
4. 成员自然完成后写回 MemberContextRef、状态 idle，并发送 completion 消息给 Lead。
5. 成员阻塞或失败时写回状态和 last_error。
6. 增加 fake runner 测试自然完成、等待审批、驳回、失败。

**验证：** 运行 uv run pytest tests/unit/test_teams_runtime.py -q，通过 worker 串联用例。

## T32: 实现合并前检查和干净合并

**文件：** src/okcode/teams/merge.py、tests/unit/test_teams_merge.py
**依赖：** T2、T7、T15
**步骤：**
1. 定义 TeamMergeManager。
2. 实现 inspect_sources(team, members)，读取成员 workdir、branch 或 source_ref。
3. 合并前检查目标工作区必须干净；不干净时拒绝合并。
4. 记录合并前 HEAD、分支和目标工作区状态。
5. 实现无冲突顺序合并，返回 clean 报告。
6. 用临时 Git 仓库测试两个成员分支干净合并。

**验证：** 运行 uv run pytest tests/unit/test_teams_merge.py -q，通过干净合并用例。

## T33: 实现冲突回滚

**文件：** src/okcode/teams/merge.py、tests/unit/test_teams_merge.py
**依赖：** T32
**步骤：**
1. 增加冲突检测，收集冲突文件列表。
2. 实现 rollback(snapshot)，优先 git merge --abort，必要时恢复到合并前 HEAD。
3. 返回 TeamMergeReport(status="rolled_back")，包含 conflict_files、source_refs 和 rollback_performed。
4. 保守处理：没有明确 resolver 时不自动编辑冲突文件。
5. 用临时 Git 仓库制造同文件冲突，确认目标仓库回到合并前状态。

**验证：** 运行 uv run pytest tests/unit/test_teams_merge.py -q，通过冲突回滚用例。

## T34: 接入 team_merge 工具

**文件：** src/okcode/teams/tools.py、src/okcode/teams/runtime.py、tests/unit/test_teams_tools.py
**依赖：** T23、T32、T33
**步骤：**
1. 在 TeamRuntime 中实现 merge(team_name, request) 委托 TeamMergeManager。
2. team_merge.inspect 返回成员来源、目标工作区是否干净、预计合并顺序。
3. team_merge.merge 返回 TeamMergeReport。
4. 合并失败和回滚报告必须能序列化为工具结果。
5. 增加 fake merge manager 测试 inspect 和 merge action。

**验证：** 运行 uv run pytest tests/unit/test_teams_tools.py -q，通过 team_merge 工具用例。

## T35: 集成 TeamRuntime 到 App

**文件：** src/okcode/app.py、src/okcode/conversation.py、tests/unit/test_app.py
**依赖：** T15、T21、T24、T25、T29
**步骤：**
1. OkCodeApp 构造时创建或接收 TeamRuntime。
2. CommandContext 增加 TeamRuntime 或通过 ConversationSession 暴露团队控制方法。
3. /team create/use/leave 后同步 UI 状态和 ConversationSession team_context。
4. 每轮模型请求前让 TeamNotificationBridge 注入当前团队通知。
5. 保持未使用团队时现有 app 流程不变。
6. 增加测试覆盖普通输入、/team create、/team leave 后工具上下文变化。

**验证：** 运行 uv run pytest tests/unit/test_app.py -q，通过 App 集成用例。

## T36: 补齐团队查询端到端测试

**文件：** tests/integration/test_team_lead_flow.py
**依赖：** T15、T18、T21、T24、T25、T26
**步骤：**
1. 使用临时 teams_root 和 fake backend 创建团队。
2. 通过命令或 runtime 创建两个成员。
3. 创建带依赖的共享任务。
4. 向成员发送 task_assignment，验证邮箱落盘和未读数量。
5. 模拟成员完成后写回 idle 状态和 completion 消息。
6. 查询团队状态，确认成员状态、任务状态、未读消息和最近更新时间可见。

**验证：** 运行 uv run pytest tests/integration/test_team_lead_flow.py -q，通过端到端团队查询用例。

## T37: 补齐审批端到端测试

**文件：** tests/integration/test_team_lead_flow.py
**依赖：** T19、T31、T36
**步骤：**
1. 创建 approval_required=True 的成员。
2. 发送 task_assignment。
3. fake member worker 先发送 approval_request 并进入 waiting_approval。
4. Lead 发送 approval_decision approved=True。
5. fake worker 恢复后继续执行并标记 idle。
6. 再覆盖 approved=False 分支，确认成员 blocked 且记录驳回原因。

**验证：** 运行 uv run pytest tests/integration/test_team_lead_flow.py -q，通过审批端到端用例。

## T38: 补齐 coordinator 集成测试

**文件：** tests/integration/test_team_lead_flow.py、tests/unit/test_teams_coordinator.py
**依赖：** T27、T28、T29、T35
**步骤：**
1. 构造 team.coordinator_enabled=False 且环境变量开启，确认 coordinator 不生效。
2. 构造 team.coordinator_enabled=True 且环境变量关闭，确认 coordinator 不生效。
3. 两把锁都打开时，确认写文件工具不可见。
4. 确认读工具、run_command、团队工具仍可见。
5. 运行一个被 guard 拒绝的写文件 shell 命令，确认返回可解释拒绝。

**验证：** 运行 uv run pytest tests/unit/test_teams_coordinator.py tests/integration/test_team_lead_flow.py -q，通过 coordinator 集成用例。

## T39: 全量回归与 lint

**文件：** 全项目
**依赖：** T1-T38
**步骤：**
1. 运行团队相关单元测试。
2. 运行现有 agents、worktrees、commands、terminal、config 相关测试。
3. 运行全量测试。
4. 运行 ruff。
5. 运行 git diff 检查，确认没有意外修改文档阶段文件以外的内容。

**验证：** 依次运行并通过：
1. uv run pytest tests/unit/test_teams_models.py tests/unit/test_teams_store.py tests/unit/test_teams_mailbox.py tests/unit/test_teams_backends.py tests/unit/test_teams_runtime.py tests/unit/test_teams_tools.py tests/unit/test_teams_coordinator.py tests/unit/test_teams_merge.py -q
2. uv run pytest tests/unit/test_agents_runner.py tests/unit/test_agents_tool.py tests/unit/test_worktrees_manager.py tests/unit/test_commands_handlers.py tests/unit/test_terminal.py tests/unit/test_config.py -q
3. uv run pytest -q
4. uv run ruff check .
5. git diff --check

## 执行顺序

T1
→ T2 → T3 → T4 → T5
→ T6 → T7 → T8
→ T9 → T10

T11
→ T12 → T13
→ T14

T6 + T7 + T8 + T9 + T12
→ T15 → T16 → T17 → T18 → T19 → T20 → T21

T15-T21
→ T22 → T23 → T24 → T25 → T26

T11 + T22
→ T27 → T28 → T29

T13 + T15 + T18 + T20
→ T30 → T31

T15
→ T32 → T33 → T34

T21 + T24 + T25 + T29
→ T35 → T36 → T37 → T38 → T39

## 自检

- plan.md 中的所有新增模块均至少对应一个任务：models、naming、paths、locking、store、mailbox、backends、runtime、tools、coordinator、worker、merge、notifications 都已覆盖。
- 所有修改文件均在文件清单和至少一个任务中出现。
- 每个任务都有明确依赖、具体步骤和验证命令。
- 任务依赖链无循环，核心顺序是先模型与持久化，再后端和 runtime，再工具与命令，最后 coordinator、worker、merge 和集成测试。
- 没有实现代码写入；本文档只定义后续开发顺序和验证方式。
