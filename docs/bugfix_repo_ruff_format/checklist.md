# 全仓 Ruff 格式检查修复 Checklist

> 每项均以命令输出、Git 差分或可观察结果为证据；格式化完成后记录实际结果。

> 验收结果（2026-08-03）：28/28 项通过。Ruff 0.16.0 恰好格式化 10 个目标文件，全仓格式检查、静态检查、定向测试、全量测试和 Git 检查均通过。

## 范围保护

- [x] 格式化前，10 个目标文件的 Git 状态为空且与 `HEAD` 无差异。（实际：两项命令均无目标差分。）
- [x] 格式化命令只接收预先识别的 10 个文件，没有使用目录、通配符或全仓写入参数。（实际：Ruff 报告 `10 files reformatted`。）
- [x] 现有 7 个会话权限修复代码与测试文件未被本次 Ruff 命令重写。（实际：格式化命令未包含这些文件，原差分保持不变。）
- [x] `pyproject.toml`、`uv.lock`、依赖和 Ruff 配置均未改变。（实际：最终差分文件清单不包含这些文件。）
- [x] 没有执行暂存、提交、重置、清理或推送操作。（实际：`git diff --cached --name-only` 为空。）

## 格式结果与语义

- [x] 使用项目锁定的 Ruff 0.16.0 生成格式差分。（实际：`uv run ruff --version` 输出 `ruff 0.16.0`。）
- [x] 原先报告的 10 个文件全部通过定向格式检查。（实际：输出 `10 files already formatted`，退出码为 0。）
- [x] 新增代码差分只出现在这 10 个目标文件中。（实际：格式化前后差分增量与目标集合完全一致。）
- [x] 10 个文件的差分只包含 Ruff 自动生成的空行、括号、折行、缩进或等价引号变化。（实际：已人工审查完整目标差分。）
- [x] Worktree 集成测试中的命令 JSON 字符串在格式化后保持等价。（实际：差分审查通过，相关测试包含在 91 项定向测试中并通过。）
- [x] 没有导入、标识符、条件、公开接口、断言值或业务语句变化。（实际：差分审查与定向测试均通过。）

## 测试与质量门禁

- [x] Agent、Conversation、App、Team 和 Worktree 定向测试通过。（实际：91 项通过。）
- [x] 会话权限核心回归测试继续通过。（实际：33 项通过。）
- [x] 全量测试通过，且不访问真实模型服务、不执行真实危险命令。（实际：460 项通过。）
- [x] 全仓 Ruff 格式检查通过，不再报告需要格式化的文件。（实际：输出 `271 files already formatted`，退出码为 0。）
- [x] 全仓 Ruff 静态检查通过。（实际：输出 `All checks passed!`。）
- [x] 最终差分不存在空白错误。（实际：`git diff --check` 退出码为 0，仅显示 Windows 行尾转换提示。）
- [x] 最终工作树只包含会话权限修复、10 个格式目标和两个规格目录内的预期文件。（实际：文件清单已核对，无未跟踪 Python 文件。）

## Spec 验收标准

- [x] AC1：全仓 `ruff format --check .` 成功通过，不再报告未格式化文件。
- [x] AC2：格式修复新增的代码差分仅限原先报告的 10 个文件，且全部为自动排版变化。
- [x] AC3：`uv run ruff check .` 成功通过。
- [x] AC4：全量测试及会话权限回归测试全部通过。
- [x] AC5：`git diff --check` 成功通过。
- [x] AC6：会话权限修复 Checklist 更新为 `47/47`，格式检查项有本轮实际证据。
- [x] AC7：没有临时文件、配置改动、暂存内容或新提交。

## 端到端场景

- [x] 场景 1——格式门禁修复：修复前全仓检查报告 10 个文件需要格式化；只对这 10 个文件运行锁定 Ruff 后，全仓格式检查退出码为 0，定向测试与全量测试均通过。
- [x] 场景 2——现有工作保护：格式化前工作树已有会话权限修复；格式化后其 7 个代码与测试文件仍只包含原有差分，权限核心回归测试保持通过。
- [x] 场景 3——字符串语义保护：Worktree 测试中的命令字符串被 Ruff 重新折行或调整引号后，实际拼接内容和测试行为保持不变。

## 完整验证命令

```powershell
uv run ruff --version
uv run ruff format --check .
uv run ruff check .
uv run pytest tests/unit/test_permissions_blacklist.py tests/unit/test_permissions_rules.py tests/unit/test_permissions_manager.py tests/unit/test_tools_executor.py -q
uv run pytest -q
git diff --check
git diff --cached --name-only
git status --short
```
