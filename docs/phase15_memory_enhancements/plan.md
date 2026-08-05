# OkCode 记忆文件格式与体积感知 Plan

## 架构概览

本功能沿用现有记忆模块和命令事件链，采用“存储层统一产出快照，命令层只传递数据，终端层负责格式化”的分层方案。`MemoryStore` 负责记忆路径、文件名安全、索引兼容、frontmatter 转换和字节统计；`ConversationSession` 将快照映射为命令端口模型；`/memory` 处理器生成已有命令事件；`TerminalUI` 将整数 bytes 转换为两位小数的 KiB。

```text
MemoryStore.snapshot()
    -> ConversationSession.memory_snapshot()
    -> CommandMemorySnapshot
    -> /memory handler
    -> CommandMemory event
    -> TerminalUI 按 bytes 格式化 KiB
```

记忆整理请求继续使用当前 Provider、无工具和禁用提示缓存。只有记忆模型 JSON 合同的标识字段从 `note_ref/title` 变为 `name/summary`；普通 Agent Loop、后台线程、会话存档和上下文注入流程不变。

## 核心数据结构

### 记忆路径

```python
MemoryPaths.for_workspace(workspace_root: Path) -> MemoryPaths
MemoryPaths.root_for(scope: MemoryScope) -> Path
MemoryPaths.index_for(scope: MemoryScope) -> Path
MemoryPaths.legacy_index_for(scope: MemoryScope) -> Path
MemoryPaths.note_for(scope: MemoryScope, name: str) -> Path
```

`index_for()` 返回 `<scope>/MEMORY.md`，`legacy_index_for()` 返回 `<scope>/index.md`。`note_for()` 是唯一的独立记忆路径入口，并在生成路径前执行跨平台名称校验。

### 体积快照

```python
@dataclass(frozen=True, slots=True)
class MemoryScopeUsage:
    files: tuple[str, ...]
    total_bytes: int

@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    project: MemoryScopeUsage
    user: MemoryScopeUsage
```

`MemoryStore.snapshot()` 只扫描两个范围根目录的普通 `.md` 文件，不递归子目录。文件名按字典序排序，大小使用 `Path.stat().st_size` 累加；目录不存在或扫描期间文件消失时跳过该项。

### 更新模型

```python
MemoryOperation(
    scope: MemoryScope,
    category: MemoryCategory,
    action: MemoryAction,
    name: str | None = None,
    summary: str = "",
    content: str = "",
)

MemoryIndexEntry(
    name: str,
    category: MemoryCategory,
    summary: str,
)
```

`category` 保留在内存模型和 LLM 合同中，供模型判断和本地校验使用；磁盘索引渲染时不写分类。创建操作要求 `name`、`summary`、`content` 非空；追加操作允许 `summary` 为空，空值表示保留已有摘要。

### 命令事件

`CommandMemorySnapshot` 和 `CommandMemory` 保留现有文件名字段，并增加：

```python
project_memory_bytes: int
user_memory_bytes: int
```

命令事件只传整数 bytes；KiB 的显示格式不进入领域模型或命令处理器。

## 模块设计

### `src/okcode/memory/models.py`

**职责：** 定义记忆范围、分类、操作、索引条目、路径和体积快照。

**改动：**

- 将 `MemoryPaths.index_for()` 切换到 `MEMORY.md`，新增 `legacy_index_for()`。
- 将路径参数由 `note_ref` 统一命名为 `name`。
- 新增 `MemoryScopeUsage` 和 `MemorySnapshot`。
- 提供跨平台 `validate_memory_name(name)`，拒绝路径分隔符、Windows 禁止字符、控制字符、结尾空格/句点、保留设备名、`MEMORY`/`index` 保留名和空名称。

### `src/okcode/memory/store.py`

**职责：** 管理两类记忆目录的索引读取、完整校验、frontmatter 写入、旧格式转换、原子更新和体积快照。

**改动：**

- 新增 `snapshot() -> MemorySnapshot` 和范围级 `.md` 文件统计辅助函数。
- `_read_index()` 先读 `MEMORY.md`，文件不存在时回退 `index.md`；不合并两份索引。
- `_render_index()` 输出无总标题的单行指针：`[name.md](name.md) | 摘要`，对链接文本做必要转义。
- 使用 `splitlines()` 计算索引行数，继续校验 200 行和 25 KB 上限。
- 新建 frontmatter 固定写入 `name`、`scope`、`category`、`summary`、`created_at`、`updated_at`。
- 追加前使用 PyYAML 解析 frontmatter；旧 `id/title` 映射为 `name/summary`，随后完整重写为新格式并追加正文；新格式只更新 `updated_at`、正文和非空摘要。
- 继续执行“先校验所有操作和候选索引，再逐文件原子替换”的流程，校验失败时不产生写入。

### `src/okcode/memory/request.py`

**职责：** 生成记忆整理请求并严格解析模型响应。

**改动：**

- 提示词将 `note_ref/title` 替换为 `name/summary`，明确 `name` 是最终 `.md` 文件名且允许中文和空格但必须满足安全规则。
- `operations` 项字段改为 `scope、category、action、name、summary、content`。
- 索引项字段改为 `name、category、summary`；两份索引仍要求是更新后的完整候选索引。
- 保留顶层字段精确校验、枚举校验、无工具和禁用缓存行为。

### `src/okcode/conversation.py`

**职责：** 为命令端口提供当前会话可见的记忆快照。

**改动：**

- `memory_snapshot()` 调用 `MemoryStore.snapshot()`，将项目/用户文件名和 bytes 映射到 `CommandMemorySnapshot`。
- 删除或停止使用会话层 `_memory_file_names()`，避免存储层和会话层出现两套扫描逻辑。
- 其他记忆上下文注入和后台 `MemoryWorker` 接入保持不变。

### `src/okcode/commands/models.py`、`src/okcode/models.py` 与 `src/okcode/commands/handlers.py`

**职责：** 维持 `/memory` 命令端口和事件结构。

**改动：**

- 两个命令快照/事件新增项目和用户 bytes 字段。
- `memory_command()` 继续只读取会话端口并构造一个 `CommandMemory` 事件，不引入目录访问。
- 现有命令注册、解析和 `/memory` 无参数行为保持不变。

### `src/okcode/terminal.py`

**职责：** 渲染用户可见的 `/memory` 输出。

**改动：**

- 保留项目/用户文件列表两行。
- 新增项目大小、用户大小、总大小三行，格式固定为 `X.XX KiB`。
- 新增局部格式化函数，将整数 bytes 除以 1024 后按两位小数输出；不存在的目录由快照层传入 0。

## 模块交互

### 普通请求的记忆读取

```text
ConversationSession 构造请求
    -> RuntimePromptContextFactory 调用 MemoryStore.read_context()
    -> MemoryStore 读取 MEMORY.md 或兼容的 index.md
    -> 记忆索引作为 long_term_memory 注入 Provider 请求
```

本次只改变索引文件的读取优先级和新格式，不读取独立记忆正文，也不改变提示词注入位置。

### 后台记忆更新

```text
MemoryWorker
    -> MemoryStore.read_indexes()
    -> MemoryRequestFactory.build/parse(name + summary 合同)
    -> MemoryStore.apply()
        -> 完整校验 name、操作、候选索引和限制
        -> 写入独立记忆文件
        -> 写入 MEMORY.md
```

`apply()` 失败只终止当前后台任务；现有 Worker 的串行队列和前台故障隔离保持不变。

### `/memory` 命令

```text
/memory
    -> memory_command()
    -> ConversationSession.memory_snapshot()
    -> MemoryStore.snapshot()
    -> CommandMemory(project files/bytes, user files/bytes)
    -> TerminalUI._render_command_memory()
```

## 文件组织

```text
docs/phase15_memory_enhancements/
├── spec.md
├── plan.md
├── task.md
└── checklist.md

src/okcode/
├── memory/models.py
├── memory/store.py
├── memory/request.py
├── conversation.py
├── commands/models.py
├── commands/handlers.py
├── models.py
└── terminal.py

tests/unit/
├── test_memory_store.py
├── test_memory_request.py
├── test_memory_worker.py
├── test_conversation.py
├── test_commands_handlers.py
└── test_terminal.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| YAML 读取 | 复用项目已有 `PyYAML.safe_load`，只解析 frontmatter | 避免手写 YAML 解析并保持现有依赖形态 |
| frontmatter 写入 | 固定字段顺序，字符串安全引用 | 输出稳定、可读，避免名称/摘要破坏 YAML |
| 索引兼容 | `MEMORY.md` 优先，缺失时读取 `index.md`，不合并 | 避免重复注入；新数据只写新索引 |
| 旧 frontmatter | `id/title` 转换为 `name/summary` 后重写 | 旧笔记可以继续追加并逐步升级 |
| 文件名安全 | 跨平台显式拒绝危险字符、保留设备名和索引保留名 | 防止路径穿越和覆盖索引 |
| 索引格式 | 每行 `[name.md](name.md) | 摘要`，不写标题/正文 | 满足简洁指针约束并保留摘要 |
| 大小计算 | 内部整数 bytes，终端格式化为 KiB | 可精确测试，避免浮点值跨层传播 |
| 扫描失败 | 目录缺失或短暂文件消失按 0/跳过 | `/memory` 不被并发文件变化阻塞 |
