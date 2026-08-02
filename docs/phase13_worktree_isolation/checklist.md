# OkCode 第十三阶段：子 Agent Worktree 隔离 Checklist

> 每一项通过运行代码、观察输出或检查文件状态验证，聚焦系统行为。四份文档全部确认后，开发阶段按本清单逐项验收。

## 实现完整性

- [ ] Worktree 模型已实现并可导入，包含身份、元数据、租约、初始化报告、退出报告、保护原因和清理报告（验证：运行 uv run python -c "from okcode.worktrees.models import WorktreeIdentity, WorktreeExitReport; print('ok')"，期望输出 ok）
- [ ] Worktree 安全名称校验拒绝路径遍历、绝对路径、空段、单点段、双点段、非法字符和超长名称（验证：运行 uv run pytest tests/unit/test_worktrees_naming.py -q，期望全部通过）
- [ ] 合法嵌套名称能派生出受管理目录名和 okcode/agents 前缀分支名（验证：运行 uv run pytest tests/unit/test_worktrees_naming.py -q -k "derive or valid"，期望全部通过）
- [ ] Git worktree client 所有 Git 命令都显式传入 cwd，且能创建、列出、检查状态和删除 worktree（验证：运行 uv run pytest tests/unit/test_worktrees_git.py -q，期望全部通过）
- [ ] Worktree 元数据能写入 .okcode/worktree.json，并在恢复时校验主仓库、common dir、managed_root、worktree_path、身份和版本（验证：运行 uv run pytest tests/unit/test_worktrees_metadata.py -q，期望全部通过）
- [ ] WorktreeManager 能创建新 worktree、写元数据、执行初始化并返回 WorktreeLease（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "prepare and create"，期望全部通过）
- [ ] 目标目录已存在且元数据匹配时，WorktreeManager 走只读快速恢复，不调用 git worktree add（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "recover"，期望 mock git add 未被调用）
- [ ] 目标目录已存在但元数据缺失或不匹配时，WorktreeManager 拒绝覆盖目录（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "metadata_mismatch or existing"，期望全部通过）

## 环境初始化

- [ ] 新建 worktree 后会复制允许的本地配置文件，且不会复制未列入 allowlist 的密钥、缓存或大型目录（验证：运行 uv run pytest tests/unit/test_worktrees_initializer.py -q -k "copy"，期望全部通过）
- [ ] 初始化重复执行保持幂等，目标文件已被子 Agent 修改时不会被覆盖（验证：运行 uv run pytest tests/unit/test_worktrees_initializer.py -q -k "idempotent or modified"，期望全部通过）
- [ ] Git hooks 配置能复用主仓库 hooksPath 或在无法复用时产生可见 warning（验证：运行 uv run pytest tests/unit/test_worktrees_initializer.py -q -k "hooks"，期望全部通过）
- [ ] .venv、.venv312、node_modules 等大型依赖目录按规则链接或明确跳过，Windows 链接失败不会静默成功（验证：运行 uv run pytest tests/unit/test_worktrees_initializer.py -q -k "link or windows"，期望全部通过）
- [ ] 被 Git 忽略但运行必需的文件只按受控规则补齐，不递归复制整个 ignored 目录（验证：运行 uv run pytest tests/unit/test_worktrees_initializer.py -q -k "ignored or runtime_file"，期望全部通过）

## 子 Agent 集成

- [ ] 角色 frontmatter 支持 isolation: shared 和 isolation: worktree，未声明时默认 shared，非法值给出 ConfigError（验证：运行 uv run pytest tests/unit/test_agents_roles.py -q -k "isolation"，期望全部通过）
- [ ] Agent 工具 schema 接受 isolation 和 worktree_name 可选字段，旧请求保持兼容（验证：运行 uv run pytest tests/unit/test_agents_tool.py -q，期望全部通过）
- [ ] defined 子 Agent 默认继承角色 isolation，fork 子 Agent 默认 shared，请求 isolation=worktree 可以升级隔离（验证：运行 uv run pytest tests/unit/test_agents_launcher.py -q -k "isolation"，期望全部通过）
- [ ] 请求 isolation=shared 不能降低角色 isolation=worktree，非法 worktree_name 会被拒绝且不会创建目录（验证：运行 uv run pytest tests/unit/test_agents_launcher.py tests/integration/test_subagent_worktree.py -q -k "downgrade or invalid"，期望全部通过）
- [ ] Hook subagent 启动路径沿用角色 isolation，不额外要求 Hook YAML 暴露 worktree_name（验证：运行 uv run pytest tests/unit/test_agents_launcher.py tests/unit/test_agents_hooks.py -q -k "hook and isolation"，期望全部通过）
- [ ] AgentRunner 在隔离模式下先 prepare worktree，再创建子运行时；prepare 失败时返回结构化任务失败且不请求模型（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q -k "prepare"，期望全部通过）
- [ ] 子 Agent 完成、失败、取消或超时时都会执行 finalize，并把 WorktreeExitReport 写入任务结果（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q -k "finalize"，期望全部通过）

## 工作区隔离

- [ ] 隔离子 Agent 的 Workspace 根目录是 worktree 绝对路径，主 Agent Workspace 根目录保持不变（验证：运行 uv run pytest tests/integration/test_subagent_worktree.py -q -k "workspace_root"，期望全部通过）
- [ ] 文件读取、写入、搜索和命令执行都作用于 worktree，不影响主工作区同名相对路径文件（验证：运行 uv run pytest tests/integration/test_subagent_worktree.py -q -k "file_isolation or command_cwd"，期望全部通过）
- [ ] 项目指令、长期记忆、上下文产物和路径相关缓存按 worktree 绝对路径加载或隔离（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/integration/test_subagent_worktree.py -q -k "instructions or memory or cache"，期望全部通过）
- [ ] 子 Agent 上下文包含 worktree 绝对路径、主工作区绝对路径、分支名、清理策略和禁止跨目录改动说明（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q -k "prompt_note"，期望断言提示内容通过）
- [ ] Fork 式隔离子 Agent 从主仓库 HEAD 创建 worktree，不自动复制主工作区未提交修改（验证：运行 uv run pytest tests/integration/test_subagent_worktree.py -q -k "fork_base_head"，期望全部通过）

## 删除保护与清理

- [ ] 无修改、无未跟踪文件、无未推送提交且状态可确认的临时 worktree 会在 finalize 时自动删除（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py tests/integration/test_subagent_worktree.py -q -k "auto_cleanup"，期望全部通过）
- [ ] 存在工作区修改、暂存区修改或未跟踪文件时，finalize 默认保留 worktree 并报告原因（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "uncommitted or untracked"，期望全部通过）
- [ ] 无 upstream 且 HEAD 已不同于 base_head 时按未推送风险保留；无 upstream 但 HEAD 等于 base_head 时不因 upstream 缺失单独阻止清理（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "upstream or base_head"，期望全部通过）
- [ ] 显式删除同样遵守删除保护；force 删除仍必须验证目标在 managed_root 内、元数据匹配且 Git worktree 状态匹配（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "delete or force"，期望全部通过）
- [ ] 后台过期清理只删除同时满足 managed path、metadata present、git worktree match 三层过滤且无变更风险的目录（验证：运行 uv run pytest tests/unit/test_worktrees_cleanup.py -q，期望全部通过）
- [ ] 清理跳过和清理失败都返回可观测原因，不会静默吞掉异常（验证：运行 uv run pytest tests/unit/test_worktrees_cleanup.py -q -k "skipped or failed"，期望全部通过）

## 可观测性

- [ ] 子 Agent 任务结果包含 isolation、worktree path、branch、cleanup decision、protection reasons 和变更摘要（验证：运行 uv run pytest tests/unit/test_agents_runner.py -q -k "worktree_result"，期望全部通过）
- [ ] 后台任务快照展示 worktree 隔离状态，shared 模式不显示空噪声字段（验证：运行 uv run pytest tests/unit/test_agents_manager.py -q -k "worktree"，期望全部通过）
- [ ] 完成通知携带 worktree 路径、分支、清理状态和保留原因，且不伪造成用户消息（验证：运行 uv run pytest tests/unit/test_agents_notifications.py -q -k "worktree"，期望全部通过）
- [ ] CLI 正确组装 WorktreeManager、WorktreeCleanupWorker 和子运行时工厂，关闭时能清理 cleanup worker（验证：运行 uv run pytest tests/unit/test_cli.py -q，期望全部通过）
- [ ] .okcode/worktrees 位于仓库内且被 Git 忽略，不会被普通版本控制追踪（验证：运行 git check-ignore .okcode/worktrees/example，期望输出该路径）

## 兼容与回归

- [ ] 未声明 isolation 的现有子 Agent 仍按第十二阶段共享工作区行为运行（验证：运行 uv run pytest tests/integration/test_subagent_turn.py -q，期望全部通过）
- [ ] 现有角色、Hook subagent、任务通知、工具过滤、权限隔离和用量统计测试不因新增字段失败（验证：运行 uv run pytest tests/unit/test_agents_*.py -q，期望全部通过）
- [ ] 未调用 Agent 工具时，普通对话、工具、权限、Skill、Hook、记忆、上下文管理和命令系统保持兼容（验证：运行 uv run pytest tests/unit/test_cli.py tests/unit/test_commands_handlers.py tests/unit/test_tools_*.py tests/unit/test_hooks_*.py -q，期望全部通过）
- [ ] Worktree 创建、恢复、初始化、删除失败会返回结构化错误，不导致主 Agent 会话崩溃（验证：运行 uv run pytest tests/unit/test_agents_runner.py tests/unit/test_worktrees_manager.py -q -k "failure or error"，期望全部通过）
- [ ] 测试不依赖真实模型服务、真实外网或危险命令，worktree 端到端使用临时 Git 仓库和假 Provider（验证：检查 tests/integration/test_subagent_worktree.py 并运行 uv run pytest tests/integration/test_subagent_worktree.py -q，期望全部通过）

## 编译与测试

- [ ] Worktree 基础设施单元测试通过（验证：运行 uv run pytest tests/unit/test_worktrees_*.py -q，期望全部通过）
- [ ] 子 Agent 接入单元测试通过（验证：运行 uv run pytest tests/unit/test_agents_roles.py tests/unit/test_agents_tool.py tests/unit/test_agents_launcher.py tests/unit/test_agents_runner.py tests/unit/test_agents_manager.py tests/unit/test_agents_notifications.py -q，期望全部通过）
- [ ] 端到端隔离与既有子 Agent 流程通过（验证：运行 uv run pytest tests/integration/test_subagent_worktree.py tests/integration/test_subagent_turn.py -q，期望全部通过）
- [ ] 全量测试通过（验证：运行 uv run pytest -q，期望全部通过）
- [ ] Ruff 格式化无待处理差异（验证：运行 uv run ruff format，随后 git diff --check，期望无格式错误）
- [ ] Ruff 静态检查通过（验证：运行 uv run ruff check，期望退出码为 0）

## 端到端场景

- [ ] 场景 1：定义式角色声明 isolation: worktree，子 Agent 修改文件后主工作区同名文件保持不变，任务结果保留 worktree 路径和变更摘要（验证：运行 uv run pytest tests/integration/test_subagent_worktree.py -q -k "defined_modifies_isolated_worktree"，期望通过）
- [ ] 场景 2：定义式角色声明 isolation: worktree，但子 Agent 不产生任何文件变更，任务完成后临时 worktree 被自动删除（验证：运行 uv run pytest tests/integration/test_subagent_worktree.py -q -k "defined_no_changes_auto_cleanup"，期望通过）
- [ ] 场景 3：Fork 请求显式 isolation=worktree，任务立即进入后台，子 Agent 工具 cwd 指向 worktree，完成通知包含分支和清理结果（验证：运行 uv run pytest tests/integration/test_subagent_worktree.py -q -k "fork_background_worktree"，期望通过）
- [ ] 场景 4：Hook 触发的子 Agent 角色声明 worktree，Hook 主流程不阻塞，后台任务完成后能查询到 worktree 状态（验证：运行 uv run pytest tests/unit/test_agents_hooks.py tests/integration/test_subagent_worktree.py -q -k "hook_worktree"，期望通过）
- [ ] 场景 5：模型传入包含 ../ 或绝对路径的 worktree_name，系统拒绝启动、没有创建目录、主 Agent 收到可行动错误（验证：运行 uv run pytest tests/integration/test_subagent_worktree.py -q -k "invalid_worktree_name"，期望通过）
- [ ] 场景 6：进程重启或任务恢复时目标 worktree 目录已存在且元数据匹配，系统复用目录，不调用 git worktree add（验证：运行 uv run pytest tests/unit/test_worktrees_manager.py -q -k "recover_existing_without_git_add"，期望通过）
- [ ] 场景 7：后台清理扫描遇到受管理目录外路径、缺元数据目录或 Git worktree 不匹配目录，全部跳过并记录原因（验证：运行 uv run pytest tests/unit/test_worktrees_cleanup.py -q -k "three_layer_filter"，期望通过）
