# OkCode 第十四阶段：Team Lead 长期团队协作 Checklist

> 每一项都必须通过运行代码、检查持久化文件、观察工具列表、查询命令输出或测试结果来验证。不能只通过阅读实现代码判断通过。

## 实现完整性

- [ ] C1: teams 包可以被导入且无副作用，对外导出稳定模型和 TeamRuntime 入口（验证：运行 uv run python -c "import okcode.teams; print('ok')"，看到 ok）。
- [ ] C2: 团队模型完整覆盖小组、成员、任务、消息、审批、后端、上下文引用、合并报告和工具上下文（验证：运行 uv run pytest tests/unit/test_teams_models.py -q，通过模型构造和字段测试）。
- [ ] C3: team_name 和 member_name 安全校验拒绝空值、路径遍历、斜杠、反斜杠、Windows 盘符、控制字符和单点双点（验证：运行 uv run pytest tests/unit/test_teams_models.py -q，通过非法名称用例）。
- [ ] C4: 团队持久化目录位于项目目录 .okcode/team/团队名 下，创建团队后生成 team.json、members.json、tasks.json、registry.json、mailboxes 和 member-sessions（验证：运行 uv run pytest tests/unit/test_teams_store.py -q，检查临时目录内文件结构）。
- [ ] C5: TeamStore 能跨实例恢复同一团队、成员花名册、任务列表和名称注册表（验证：运行 uv run pytest tests/unit/test_teams_store.py -q，通过创建后重新加载用例）。
- [ ] C6: 共享任务支持创建、查询、更新、关闭、依赖字段和状态字段，更新任务不会覆盖其他任务或其他字段（验证：运行 uv run pytest tests/unit/test_teams_store.py tests/unit/test_teams_runtime.py -q）。
- [ ] C7: 邮箱消息落盘时自动补 message_id、created_at、read=False 和 summary，读取未读和标记已读行为稳定（验证：运行 uv run pytest tests/unit/test_teams_mailbox.py -q）。
- [ ] C8: 邮箱和共享任务写入使用锁文件保护，锁等待超时、陈旧锁接管和释放锁均有可诊断结果（验证：运行 uv run pytest tests/unit/test_teams_mailbox.py tests/unit/test_teams_store.py -q）。
- [ ] C9: 广播消息对每个成员分别返回成功或失败结果，单个目标失败不会回滚其他成员已写入邮箱（验证：运行 uv run pytest tests/unit/test_teams_mailbox.py tests/unit/test_teams_runtime.py -q）。
- [ ] C10: 后端选择支持 terminal_pane 和 coroutine，显式强隔离不可用时失败并说明缺失能力，auto 模式会展示实际选择（验证：运行 uv run pytest tests/unit/test_teams_backends.py -q）。
- [ ] C11: 终端窗格后端能通过可测试 controller 完成 spawn、wake、terminate；wake 失败时消息仍保留为已落盘但未唤醒（验证：运行 uv run pytest tests/unit/test_teams_backends.py tests/unit/test_teams_runtime.py -q）。
- [ ] C12: 协程后端能创建稳定 handle、唤醒待运行成员并终止待运行任务，不复用 Lead 的对话状态（验证：运行 uv run pytest tests/unit/test_teams_backends.py -q）。
- [ ] C13: TeamRuntime 能创建团队、恢复团队、添加成员、查询 snapshot，并返回成员状态、未读消息数量、任务摘要和可恢复性状态（验证：运行 uv run pytest tests/unit/test_teams_runtime.py -q）。
- [ ] C14: TeamRuntime 发送点对点消息时必须通过名称注册表解析目标成员，目标不存在返回结构化失败（验证：运行 uv run pytest tests/unit/test_teams_runtime.py -q）。
- [ ] C15: 审批成员收到任务后先进入 waiting_approval，并生成 approval_request；批准后继续执行，驳回后 blocked 并记录原因（验证：运行 uv run pytest tests/unit/test_teams_runtime.py tests/integration/test_team_lead_flow.py -q）。
- [ ] C16: 成员自然完成后写回 MemberContextRef、状态 idle，并向 Lead 发送 completion 或 idle 通知（验证：运行 uv run pytest tests/unit/test_teams_runtime.py tests/integration/test_team_lead_flow.py -q）。
- [ ] C17: Lead 向 idle 或停止成员发送新消息后，restore_member 能从磁盘上下文引用恢复或返回缺失项诊断（验证：运行 uv run pytest tests/unit/test_teams_runtime.py -q）。
- [ ] C18: 团队通知桥把未读消息、审批请求、阻塞上报、成员空闲和合并结果转成 SystemInstruction，且不伪造成用户消息（验证：运行 uv run pytest tests/unit/test_teams_runtime.py -q，并检查通知 kind 与 priority）。
- [ ] C19: team_task、team_message、team_member、team_merge 四个模型工具的名称、schema、安全等级和 timeout 稳定（验证：运行 uv run pytest tests/unit/test_teams_tools.py -q）。
- [ ] C20: 团队工具 action 能正确调用 TeamRuntime 或 TeamMergeManager，并在缺少 TeamToolContext 时返回不可用错误（验证：运行 uv run pytest tests/unit/test_teams_tools.py -q）。
- [ ] C21: Team Lead 上下文能看到四个团队工具，Team Member 默认只看到 team_task 和 team_message，普通会话和普通子 Agent 看不到团队工具（验证：运行 uv run pytest tests/unit/test_teams_tools.py -q）。
- [ ] C22: /team create、/team use、/team leave、/team status 命令能正确切换或查询团队上下文，且 /team 空命令有明确状态输出（验证：运行 uv run pytest tests/unit/test_commands_handlers.py tests/unit/test_app.py -q）。
- [ ] C23: 终端能渲染团队状态、成员状态、未读消息数量、阻塞任务和 coordinator 状态，空状态有明确提示（验证：运行 uv run pytest tests/unit/test_terminal.py -q）。
- [ ] C24: team.coordinator_enabled 和 OKCODE_COORDINATOR=1 必须同时存在才启用 coordinator，任意一把锁关闭都保持普通模式（验证：运行 uv run pytest tests/unit/test_teams_coordinator.py -q）。
- [ ] C25: coordinator 生效时，直接写文件工具不可见，读类工具、run_command、团队工具和合并工具仍可见（验证：运行 uv run pytest tests/unit/test_teams_coordinator.py -q）。
- [ ] C26: CoordinatorCommandGuard 拒绝明显写文件 shell 命令，允许只读检查和 Git 合并相关命令继续进入权限系统（验证：运行 uv run pytest tests/unit/test_teams_coordinator.py -q）。
- [ ] C27: 独立成员 worker 能加载团队、成员和邮箱，能处理未读任务、审批状态、完成状态和失败状态（验证：运行 uv run pytest tests/unit/test_teams_backends.py tests/unit/test_teams_runtime.py -q）。
- [ ] C28: TeamMergeManager 在目标工作区干净时能顺序合并多个成员来源，并返回 clean 或 auto_resolved 报告（验证：运行 uv run pytest tests/unit/test_teams_merge.py -q）。
- [ ] C29: 合并遇到不可安全处理冲突时会回滚到合并前状态，并报告冲突文件、成员产出引用和 rollback_performed=True（验证：运行 uv run pytest tests/unit/test_teams_merge.py -q）。

## 集成检查

- [ ] C30: 未使用团队时，普通对话、现有 agent 工具、Hook 子 Agent、worktree 隔离、命令系统、权限系统、上下文管理、Skill 和 MCP 行为保持不变（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/unit/test_agents_tool.py tests/unit/test_worktrees_manager.py tests/unit/test_commands_handlers.py tests/unit/test_config.py -q）。
- [ ] C31: 团队工具注入发生在 TeamToolContext 存在之后，普通 registry 的默认构造不包含团队工具（验证：运行 uv run pytest tests/unit/test_teams_tools.py -q，并断言 build_default_registry 输出不含 team_task）。
- [ ] C32: 团队通知和 Phase 12 子 Agent 通知一样通过 SystemInstruction 注入，不写成 Role.USER 或普通 ChatMessage（验证：运行 uv run pytest tests/unit/test_teams_runtime.py tests/unit/test_conversation_subagents.py -q）。
- [ ] C33: 成员工作目录和受管理 worktree 能与现有 WorktreeManager 共存，路径绑定工具仍指向成员工作目录而不是 Lead 工作目录（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py tests/unit/test_teams_runtime.py -q）。
- [ ] C34: 审批未完成前，成员运行时只拥有读类工具和团队消息工具；审批通过后才恢复完整成员工具集（验证：运行 uv run pytest tests/unit/test_teams_runtime.py tests/integration/test_team_lead_flow.py -q）。
- [ ] C35: terminal_pane 消息发送顺序固定为先写邮箱再唤醒窗格，唤醒失败不丢消息（验证：运行 uv run pytest tests/unit/test_teams_runtime.py -q）。
- [ ] C36: /team status 展示的小组、成员、任务和未读消息与磁盘持久化文件一致（验证：运行 uv run pytest tests/integration/test_team_lead_flow.py -q）。
- [ ] C37: coordinator 模式不修改权限系统语义；被保留的 run_command 仍需经过权限系统和 guard（验证：运行 uv run pytest tests/unit/test_teams_coordinator.py tests/unit/test_permissions_manager.py -q）。
- [ ] C38: team_merge 工具的 inspect 和 merge action 能完整序列化成功、失败、回滚和冲突报告（验证：运行 uv run pytest tests/unit/test_teams_tools.py tests/unit/test_teams_merge.py -q）。

## Spec 验收覆盖

- [ ] AC1: 创建小组后，用户目录下出现按小组名隔离的持久化目录，包含小组元数据、成员元数据、共享任务列表、名称注册表和邮箱；重启 TeamStore 后仍能查询到同一小组和成员花名册（验证：运行 uv run pytest tests/unit/test_teams_store.py tests/unit/test_teams_runtime.py -q）。
- [ ] AC2: 支持终端窗格的 fake 环境中强隔离成员选择 terminal_pane；不支持且不允许降级时失败；auto 模式展示实际选择后端（验证：运行 uv run pytest tests/unit/test_teams_backends.py -q）。
- [ ] AC3: 普通会话和普通子 Agent 工具列表没有团队工具；Team Lead 和 Team Member 只看到各自允许的团队工具；coordinator 生效时写文件工具不可见并显示 coordinator 状态（验证：运行 uv run pytest tests/unit/test_teams_tools.py tests/unit/test_teams_coordinator.py tests/unit/test_terminal.py -q）。
- [ ] AC4: Lead 能创建带 dependencies 的多条共享任务；多个成员更新不同任务后，tasks.json 保持完整可解析且不丢字段（验证：运行 uv run pytest tests/unit/test_teams_store.py tests/integration/test_team_lead_flow.py -q）。
- [ ] AC5: 点对点消息通过名称注册表找到目标邮箱，自动补时间戳和默认未读，用锁文件写入；广播返回每个目标结果；结构化协议消息能写入并解析（验证：运行 uv run pytest tests/unit/test_teams_mailbox.py tests/unit/test_teams_runtime.py -q）。
- [ ] AC6: 独立终端成员收到消息时先落盘再唤醒；唤醒失败结果显示已落盘但未唤醒，目标邮箱仍有未读消息（验证：运行 uv run pytest tests/unit/test_teams_backends.py tests/unit/test_teams_runtime.py -q）。
- [ ] AC7: 需要审批的成员先发送计划审批请求；未批准前不能执行写入或高风险操作；批准后继续，驳回后停止并记录原因（验证：运行 uv run pytest tests/unit/test_teams_runtime.py tests/integration/test_team_lead_flow.py -q）。
- [ ] AC8: 成员自然完成后状态变为空闲并通知 Lead；Lead 后续发消息能恢复成员上下文；恢复失败时返回缺失上下文、后端或工作目录的具体原因（验证：运行 uv run pytest tests/unit/test_teams_runtime.py tests/integration/test_team_lead_flow.py -q）。
- [ ] AC9: 多个成员代码修改完成后，Lead 能发起合并；无冲突进入目标工作目录；可安全处理冲突记录依据；不可处理冲突回滚并报告冲突文件和成员产出引用（验证：运行 uv run pytest tests/unit/test_teams_merge.py tests/unit/test_teams_tools.py -q）。
- [ ] AC10: 只开配置或只开环境变量时 coordinator 不生效；两者都开时才生效；生效后写工具不可见，读类工具、必要 shell、派人、终止、发消息和合并仍可用（验证：运行 uv run pytest tests/unit/test_teams_coordinator.py -q）。
- [ ] AC11: 团队查询入口展示小组、成员、任务和消息状态，包括未读消息数量、阻塞任务、成员可恢复性和最近更新时间；关键团队事件可在测试中观察（验证：运行 uv run pytest tests/integration/test_team_lead_flow.py tests/unit/test_terminal.py -q）。
- [ ] AC12: 新增测试覆盖邮箱锁、陈旧锁接管、并发任务更新、审批流程、成员恢复、coordinator 工具过滤和合并回滚；全量现有测试保持通过（验证：运行 uv run pytest -q 和 uv run ruff check .）。

## 端到端场景

- [ ] E2E1: 用户执行 /team create core-team 后，当前会话成为 Team Lead，/team status 展示空团队状态，模型工具列表出现 team_task、team_message、team_member、team_merge（验证：运行 uv run pytest tests/integration/test_team_lead_flow.py -q）。
- [ ] E2E2: Lead 创建两个成员，写入两个带依赖关系的任务，并向成员发送 task_assignment；两个成员邮箱出现未读结构化消息，团队状态显示对应任务和未读数量（验证：运行 uv run pytest tests/integration/test_team_lead_flow.py -q）。
- [ ] E2E3: approval_required 成员收到任务后只提交 approval_request，Lead 批准后成员继续执行并转为空闲；Lead 驳回另一个请求后成员转为 blocked（验证：运行 uv run pytest tests/integration/test_team_lead_flow.py -q）。
- [ ] E2E4: idle 成员收到 Lead 新消息后，restore_member 使用 MemberContextRef 恢复上下文继续执行，而不是创建无历史新成员（验证：运行 uv run pytest tests/integration/test_team_lead_flow.py -q）。
- [ ] E2E5: 终端窗格成员消息已写入但唤醒失败时，发送报告显示已落盘但未唤醒，后续手动恢复仍能读到原未读消息（验证：运行 uv run pytest tests/unit/test_teams_runtime.py tests/unit/test_teams_backends.py -q）。
- [ ] E2E6: 多成员分支干净合并后，目标仓库包含成员变更并记录来源；制造冲突时合并回滚，目标仓库回到合并前 HEAD（验证：运行 uv run pytest tests/unit/test_teams_merge.py -q）。
- [ ] E2E7: coordinator 双锁开启后，Lead 只能协调、派人、审批、消息、终止和合并；直接写文件工具不可见，写文件 shell 被 guard 拒绝（验证：运行 uv run pytest tests/unit/test_teams_coordinator.py tests/integration/test_team_lead_flow.py -q）。

## 编译、测试与质量

- [ ] Q1: 团队相关单元测试全部通过（验证：运行 uv run pytest tests/unit/test_teams_models.py tests/unit/test_teams_store.py tests/unit/test_teams_mailbox.py tests/unit/test_teams_backends.py tests/unit/test_teams_runtime.py tests/unit/test_teams_tools.py tests/unit/test_teams_coordinator.py tests/unit/test_teams_merge.py -q）。
- [ ] Q2: 关键回归测试通过，覆盖 agents、worktrees、commands、terminal、config、permissions 和 conversation（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/unit/test_agents_tool.py tests/unit/test_worktrees_manager.py tests/unit/test_commands_handlers.py tests/unit/test_terminal.py tests/unit/test_config.py tests/unit/test_permissions_manager.py tests/unit/test_conversation_subagents.py -q）。
- [ ] Q3: 端到端团队流程测试通过（验证：运行 uv run pytest tests/integration/test_team_lead_flow.py -q）。
- [ ] Q4: 全量测试通过（验证：运行 uv run pytest -q）。
- [ ] Q5: Ruff 检查通过（验证：运行 uv run ruff check .）。
- [ ] Q6: Git diff 没有空白错误或冲突标记（验证：运行 git diff --check）。
- [ ] Q7: 当前变更没有越过本阶段范围，不包含跨机器分布式、实时流式通信、复杂自动依赖调度或图形化看板（验证：检查 git status --short 和 git diff --stat，确认变更集中在 phase14 任务文件列出的模块）。

## 自检

- spec.md 的 AC1-AC12 均已在 Spec 验收覆盖中逐条映射。
- plan.md 的核心组件均有对应实现完整性或集成检查项。
- checklist 至少包含一个完整 Team Lead 流程、一个审批流程、一个 coordinator 流程和一个合并回滚流程。
- 所有条目都包含可运行命令或可观察输出。
- 没有实现代码写入；本文档只定义验收标准。
