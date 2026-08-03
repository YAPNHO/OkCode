# 全仓 Ruff 格式检查修复 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/okcode/agents/__init__.py` | Ruff 空行格式化 |
| 修改 | `src/okcode/agents/runtime.py` | Ruff 表达式折行格式化 |
| 修改 | `src/okcode/conversation.py` | Ruff 条件与生成式折行格式化 |
| 修改 | `src/okcode/teams/runtime.py` | Ruff 生成式折行格式化 |
| 修改 | `src/okcode/teams/store.py` | Ruff 字典与空行格式化 |
| 修改 | `tests/integration/test_subagent_worktree.py` | Ruff 字符串拼接和断言折行格式化 |
| 修改 | `tests/unit/test_app.py` | Ruff 条件断言折行格式化 |
| 修改 | `tests/unit/test_teams_merge.py` | Ruff 调用表达式折行格式化 |
| 修改 | `tests/unit/test_teams_store.py` | Ruff 条件表达式折行格式化 |
| 修改 | `tests/unit/test_teams_tools.py` | Ruff 集合推导式和字符串折行格式化 |
| 修改 | `docs/bugfix_session_permission/checklist.md` | 验证成功后记录 47/47 通过 |
| 新建 | `docs/bugfix_repo_ruff_format/spec.md` | 格式修复需求规格 |
| 新建 | `docs/bugfix_repo_ruff_format/plan.md` | 格式修复技术设计 |
| 新建 | `docs/bugfix_repo_ruff_format/task.md` | 格式修复任务拆解 |
| 新建 | `docs/bugfix_repo_ruff_format/checklist.md` | 格式修复验收清单 |

## T1：锁定格式化范围并保护现有工作

**文件：** 上述 10 个格式化目标、当前工作树全部改动

**依赖：** 无

**步骤：**

1. 记录 `git status --short` 的完整输出，确认当前会话权限修复的 7 个修改文件和两个规格目录仍在。
2. 对 10 个格式化目标运行 `git status --short -- <目标列表>`，确认输出为空。
3. 对同一目标列表运行 `git diff HEAD --name-only -- <目标列表>`，确认输出为空。
4. 若目标文件已经出现用户或并发任务改动，停止执行，不覆盖现有内容。

**验证：** 10 个目标在格式化前与 `HEAD` 完全一致；受保护的权限修复文件仍保持原状态。

## T2：使用锁定 Ruff 格式化 10 个目标

**文件：** 10 个格式化目标

**依赖：** T1

**步骤：**

1. 运行 `uv run ruff --version`，确认使用 `uv.lock` 当前解析的版本。
2. 运行 `uv run ruff format <10 个显式文件路径>`，不传入目录、通配符或 `.`。
3. 重新运行 `git status --short -- <目标列表>`，确认恰好 10 个目标产生修改。
4. 运行 `uv run ruff format --check <10 个显式文件路径>`。

**验证：** Ruff 报告 10 个目标均已格式化，定向格式检查退出码为 0。

## T3：审查格式差分与字符串语义

**文件：** 10 个格式化目标

**依赖：** T2

**步骤：**

1. 运行 `git diff -- <10 个目标>`，逐个检查差分。
2. 确认差分类型只包含空行、括号、折行、缩进和 Ruff 的等价引号规范化。
3. 重点检查 `tests/integration/test_subagent_worktree.py` 中命令 JSON 字符串的拼接结果，确认字符顺序、空格和转义保持等价。
4. 确认没有导入变化、标识符变化、条件变化、断言值变化或新增删除业务语句。

**验证：** 人工差分审查确认 10 个文件均为自动排版变化，没有逻辑差分。

## T4：运行受影响模块与权限回归测试

**文件：** Agent、Conversation、Team、Worktree 和 Permission 相关测试

**依赖：** T3

**步骤：**

1. 运行 Agent、Conversation、App、Team 和 Worktree 相关测试：

   ```powershell
   uv run pytest tests/unit/test_agents_runner.py tests/unit/test_app.py tests/unit/test_conversation.py tests/unit/test_teams_backends.py tests/unit/test_teams_coordinator.py tests/unit/test_teams_mailbox.py tests/unit/test_teams_merge.py tests/unit/test_teams_models.py tests/unit/test_teams_runtime.py tests/unit/test_teams_store.py tests/unit/test_teams_tools.py tests/integration/test_subagent_worktree.py -q
   ```

2. 运行会话权限修复的核心测试组，确认并行工作树中的功能修复仍然通过：

   ```powershell
   uv run pytest tests/unit/test_permissions_blacklist.py tests/unit/test_permissions_rules.py tests/unit/test_permissions_manager.py tests/unit/test_tools_executor.py -q
   ```

3. 若定向测试失败，先定位是否由字符串格式化或组合工作树造成，不直接修改业务逻辑。

**验证：** 所有定向测试通过；没有真实模型调用或真实危险命令执行。

## T5：运行全仓质量门禁

**文件：** 全仓源码与测试

**依赖：** T4

**步骤：**

1. 运行 `uv run ruff format --check .`。
2. 运行 `uv run ruff check .`。
3. 运行 `uv run pytest -q`。
4. 运行 `git diff --check`。
5. 检查 `git diff --name-only` 和 `git status --short`，确认没有临时文件、配置变化、暂存内容或意外文件。

**验证：** 两项 Ruff 检查、全量测试和 Git 空白检查均以退出码 0 完成；文件清单符合 Spec。

## T6：更新验收记录并最终复核

**文件：** `docs/bugfix_session_permission/checklist.md`、`docs/bugfix_repo_ruff_format/checklist.md`

**依赖：** T5

**步骤：**

1. 把会话权限 Checklist 的结果从 `46/47` 更新为 `47/47`。
2. 将原先唯一未通过的全仓 Ruff 格式检查项标记为通过，记录本轮实际输出。
3. 按新格式修复 Checklist 逐项记录证据并标记实际状态。
4. 再运行 `uv run ruff format --check .` 与 `git diff --check`，确认文档更新未引入新问题。
5. 输出最终差分文件清单，不暂存、不提交。

**验证：** 两份 Checklist 与实际命令结果一致，最终格式检查和空白检查通过，Git 暂存区为空。

## 执行顺序

```text
T1 -> T2 -> T3 -> T4 -> T5 -> T6
```
