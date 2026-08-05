# OkCode 记忆文件格式与体积感知 Tasks

> 本任务拆解以已批准的 `spec.md` 和 `plan.md` 为实现基线。开发开始前必须继续完成并审批 `checklist.md`；四份文档全部审批前禁止编写实现代码。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/okcode/memory/models.py` | `name`、路径、快照和名称校验 |
| 修改 | `src/okcode/memory/store.py` | 新索引、frontmatter 转换、体积统计 |
| 修改 | `src/okcode/memory/request.py` | 新 JSON 合同和提示词 |
| 修改 | `src/okcode/conversation.py` | 使用 `MemoryStore.snapshot()` |
| 修改 | `src/okcode/commands/models.py` | 命令快照增加 bytes |
| 修改 | `src/okcode/models.py` | `CommandMemory` 增加 bytes |
| 修改 | `src/okcode/terminal.py` | KiB 输出 |
| 修改 | `tests/unit/test_memory_store.py` | 存储、格式、兼容和统计测试 |
| 修改 | `tests/unit/test_memory_request.py` | `name/summary` 合同测试 |
| 修改 | `tests/unit/test_memory_worker.py` | Worker 新合同回归 |
| 修改 | `tests/unit/test_conversation.py` | 会话快照和记忆注入回归 |
| 修改 | `tests/unit/test_commands_handlers.py` | 命令快照字段测试 |
| 修改 | `tests/unit/test_terminal.py` | `/memory` 完整渲染测试 |
| 新建 | `docs/phase15_memory_enhancements/checklist.md` | 验收检查项 |

## T1：更新记忆模型和路径

**文件：** `src/okcode/memory/models.py`、`tests/unit/test_memory_store.py`  
**依赖：** 无

**步骤：**

1. 将 `MemoryPaths` 的新索引路径改为 `MEMORY.md`，新增旧索引路径。
2. 将 `note_for()` 参数由 `note_ref` 改为 `name`。
3. 新增 `MemoryScopeUsage`、`MemorySnapshot`。
4. 新增跨平台名称校验及 `MEMORY`/`index` 索引保留名规则。
5. 更新路径和模型构造测试，覆盖中文、空格、危险字符和保留名。

**验证：** `uv run pytest tests/unit/test_memory_store.py -q -k "paths or name"`，期望路径映射正确且非法名称全部被拒绝。

## T2：改造存储层和兼容写入

**文件：** `src/okcode/memory/store.py`、`tests/unit/test_memory_store.py`  
**依赖：** T1

**步骤：**

1. 实现 `MemoryStore.snapshot()` 和 `.md` bytes 累加。
2. 实现 `MEMORY.md` 优先、`index.md` 回退读取，不合并两份索引。
3. 将索引渲染改为每行指针和摘要，无总标题、无分类字段。
4. 将新建 frontmatter 改为 `name/summary` 格式。
5. 使用 PyYAML 读取旧 frontmatter，追加时完成 `id/title -> name/summary` 转换。
6. 保留原子写入、索引行数和字节限制，并使用 `splitlines()` 计算行数。
7. 增加新索引路径、索引格式、frontmatter、统计值、旧格式追加和失败无写入测试。

**验证：** `uv run pytest tests/unit/test_memory_store.py -q`，期望新旧格式、统计和限制测试全部通过。

## T3：更新记忆请求合同

**文件：** `src/okcode/memory/request.py`、`tests/unit/test_memory_request.py`、`tests/unit/test_memory_worker.py`  
**依赖：** T1

**步骤：**

1. 更新内部提示词中的字段说明和 `name` 文件名安全约束。
2. 将操作字段改为 `scope、category、action、name、summary、content`。
3. 将索引条目字段改为 `name、category、summary`。
4. 保留严格顶层字段、枚举、无工具和禁用缓存校验。
5. 更新请求解析测试和 Worker 响应夹具，覆盖 create、append、noop。

**验证：** `uv run pytest tests/unit/test_memory_request.py tests/unit/test_memory_worker.py -q`，期望新合同解析和串行 Worker 流程全部通过。

## T4：接入会话和命令快照

**文件：** `src/okcode/conversation.py`、`src/okcode/commands/models.py`、`src/okcode/models.py`、`src/okcode/commands/handlers.py`、相关测试  
**依赖：** T2

**步骤：**

1. 将 `ConversationSession.memory_snapshot()` 改为消费 `MemoryStore.snapshot()`。
2. 删除或停止使用会话层重复的 `_memory_file_names()` 扫描。
3. 扩展 `CommandMemorySnapshot` 和 `CommandMemory` 的项目/用户 bytes 字段。
4. 保持 `memory_command()` 只通过会话端口取数据，不访问文件系统。
5. 更新命令 Handler 和会话测试夹具，补充 bytes 和总量字段断言。

**验证：** `uv run pytest tests/unit/test_conversation.py tests/unit/test_commands_handlers.py -q`，期望长期记忆注入和 `/memory` 命令事件回归通过。

## T5：更新终端 `/memory` 展示

**文件：** `src/okcode/terminal.py`、`tests/unit/test_terminal.py`  
**依赖：** T4

**步骤：**

1. 保留项目/用户文件列表展示和空列表的 `（无）` 行为。
2. 增加项目大小、用户大小、总大小三行。
3. 新增 bytes 到两位小数 KiB 的局部格式化逻辑。
4. 覆盖空目录、中文文件名、0 bytes 和大于 1 KiB 的格式化结果。

**验证：** `uv run pytest tests/unit/test_terminal.py -q -k memory`，期望完整输出包含文件列表、两个范围大小和总大小。

## T6：补齐跨模块回归测试

**文件：** `tests/unit/test_memory_store.py`、`tests/unit/test_memory_request.py`、`tests/unit/test_memory_worker.py`、`tests/unit/test_conversation.py`、`tests/unit/test_commands_handlers.py`、`tests/unit/test_terminal.py`  
**依赖：** T2、T3、T4、T5

**步骤：**

1. 更新所有旧 `note_ref/title/index.md` 测试夹具到新合同或兼容场景。
2. 增加从存储快照到 `/memory` 渲染事件的无网络端到端测试。
3. 增加索引超限时“无任何写入”的回归测试。
4. 增加旧索引和旧 frontmatter 读取、追加、新索引生成测试。
5. 确认长期记忆上下文注入仍只读取索引，不读取独立正文。

**验证：**

```text
uv run pytest tests/unit/test_memory_store.py tests/unit/test_memory_request.py tests/unit/test_memory_worker.py tests/unit/test_conversation.py tests/unit/test_commands_handlers.py tests/unit/test_terminal.py -q
```

期望所有目标测试通过，且无真实网络或 Provider 依赖。

## T7：静态检查和全量回归

**文件：** 本任务涉及的全部源文件和测试文件  
**依赖：** T6

**步骤：**

1. 运行全量测试并记录实际通过数。
2. 运行 Ruff 静态检查。
3. 运行 `git diff --check`。
4. 根据失败证据修复，再重新执行对应任务验证和全量检查。

**验证：**

```text
uv run pytest -q
uv run ruff check .
git diff --check
```

## 执行顺序

```text
T1 -> T2 -> T4 -> T5
  \\-> T3 --------/
T2/T3/T4/T5 -> T6 -> T7
```
