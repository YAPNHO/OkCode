# 全仓 Ruff 格式检查修复 Plan

## 架构概览

本次不改变运行时架构，只执行一次受控的源码格式迁移。以当前 `uv.lock` 解析出的 Ruff 0.16.0 为唯一格式化器，显式传入已经由全仓检查识别出的 10 个文件，避免对其他文件进行意外重写。

执行链路如下：

```text
核对 10 个目标文件与 HEAD 一致
  -> 记录当前工作树文件清单
  -> Ruff 只格式化 10 个显式目标
  -> 审查这 10 个文件的差分
  -> 全仓 Ruff format/check
  -> 定向测试与全量测试
  -> Git 空白及工作树范围检查
  -> 更新会话权限修复 Checklist
```

## 核心输入与约束

### 格式化目标集合

格式化命令只允许接收以下 10 个文件：

```text
src/okcode/agents/__init__.py
src/okcode/agents/runtime.py
src/okcode/conversation.py
src/okcode/teams/runtime.py
src/okcode/teams/store.py
tests/integration/test_subagent_worktree.py
tests/unit/test_app.py
tests/unit/test_teams_merge.py
tests/unit/test_teams_store.py
tests/unit/test_teams_tools.py
```

目标集合在格式化前与 `git status`、`git diff HEAD` 对照，确认它们尚未包含用户的未提交修改。若任一目标文件在执行前出现新的非格式差分，则停止格式化并重新核对，不覆盖并发修改。

### 受保护文件集合

现有会话权限修复的 7 个代码与测试文件属于受保护集合，不传给格式化命令。格式化后重新对照差分文件列表，确认它们只保留原有权限修复变化，没有因本任务产生额外格式差分。

### 验收状态记录

只有全仓格式检查、静态检查和全量测试全部成功后，才将 `docs/bugfix_session_permission/checklist.md` 中唯一未通过的格式检查项改为通过，并把总结果从 `46/47` 更新为 `47/47`。

## 模块设计

### Ruff 格式执行

**职责：** 通过项目锁定版本生成确定性的排版变更。

**接口：** 使用 `uv run ruff format`，参数为上述 10 个显式路径。

**依赖：** `pyproject.toml` 中现有 Ruff 配置与 `uv.lock` 中锁定的 Ruff 0.16.0。

**约束：** 写入阶段只传入显式文件路径，不使用规则修改参数，不运行 `ruff check --fix`，不修改配置文件。

### 差分审查

**职责：** 证明新增差分仅为自动排版变化。

**检查方式：**

- 对格式化目标运行 `git diff -- <10 个目标>`，逐项检查折行、括号、空行和引号规范化。
- 对比格式化前后的差分文件集合，排除意外文件。
- 特别检查 `tests/integration/test_subagent_worktree.py` 中被 Ruff 重排的字符串拼接，确认拼接后的命令字符串保持一致。

### 回归验证

**职责：** 排除格式化导致的语法、字符串或测试语义变化。

**验证层级：**

1. 运行 10 个文件直接覆盖的 Agent、Conversation、Team 和 Worktree 测试。
2. 运行会话权限定向测试，确认并行存在的权限修复仍然通过。
3. 运行全量测试。
4. 运行全仓 Ruff 格式检查、静态检查和 `git diff --check`。

## 模块交互

1. Git 只用于读取状态和差分，不执行暂存、提交、还原或清理。
2. Ruff 读取现有配置并只写入显式的 10 个格式化目标。
3. pytest 读取格式化后的源码和当前未提交的权限修复，验证组合工作树的实际状态。
4. 验收命令成功后，更新原权限修复 Checklist；若任一关键检查失败，保留真实未通过状态并先定位原因。

## 文件组织

```text
docs/bugfix_repo_ruff_format/
├── spec.md
├── plan.md
├── task.md
└── checklist.md

docs/bugfix_session_permission/checklist.md   # 验收通过后更新结果

src/okcode/agents/__init__.py
src/okcode/agents/runtime.py
src/okcode/conversation.py
src/okcode/teams/runtime.py
src/okcode/teams/store.py
tests/integration/test_subagent_worktree.py
tests/unit/test_app.py
tests/unit/test_teams_merge.py
tests/unit/test_teams_store.py
tests/unit/test_teams_tools.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 格式化器 | 当前锁定的 Ruff 0.16.0 | 与失败的质量门禁使用同一实现，结果可复现 |
| 修改范围 | 显式列出 10 个文件 | 防止全仓写命令意外触碰已经合规或正在修改的文件 |
| 差分生成 | Ruff 自动格式化 | 避免人工排版与工具结果不一致 |
| 静态修复 | 不运行 `ruff check --fix` | 本任务只处理格式，不引入额外 lint 修改 |
| 回归范围 | 定向测试加全量测试 | 同时覆盖字符串重排风险和组合工作树行为 |
| Git 操作 | 只读检查 | 保留用户现有未提交工作，不扩大授权到暂存或提交 |
| Checklist 更新 | 所有门禁通过后更新 | 保证记录的是实际结果而非预期结果 |

## Spec 覆盖

| Spec 项 | 设计归属 |
|---|---|
| F1 | 显式 10 文件目标集合与 Ruff 格式执行 |
| F2 | 差分审查和定向测试 |
| F3 | 受保护文件集合与差分文件对照 |
| F4 | 验收状态记录 |
| F5 | Git 只读约束 |
| N1-N2 | 锁定 Ruff 版本且不修改配置或依赖 |
| N3 | 四层回归验证 |
| N4 | 最终文件清单与差分审查 |
| AC1-AC7 | 全仓格式、静态、测试、空白、Checklist 和状态验证 |
