# OkCode 第八阶段：会话恢复与长期记忆 Plan

## 架构总览

第八阶段新增指令、会话存档和长期记忆三个领域模块，`ConversationSession` 保持 Agent Loop 与消息提交的所有权，只在明确边界调用它们。普通启动创建一个惰性的新会话日志，不恢复旧消息；第一轮请求通过现有 `PromptOptionalSections` 注入分层项目指令和长期记忆索引。若用户输入 `/resume`，应用层展示可恢复会话并协调会话对象回载选中的历史。

```text
启动
  -> InstructionLoader 加载三层 AGENTS.md
  -> SessionStore 清理过期 JSONL，创建惰性新日志
  -> MemoryStore 读取用户级和项目级索引
  -> ConversationSession 构建请求时注入指令和索引

自然结束并成功提交一轮
  -> ConversationSession 追加本轮消息到 SessionJournal
  -> MemoryWorker 后台串行处理 MemoryJob
      -> 使用当前 Provider 配置创建独立 LLM 客户端
      -> MemoryRequestFactory 构建无工具、无缓存请求
      -> MemoryStore 原子更新 Markdown 笔记和索引

/resume
  -> OkCodeApp 向 SessionStore 查询摘要列表
  -> TerminalUI 显示列表并读取用户选择
  -> ConversationSession 读取有效 JSONL 前缀并重置上下文状态
  -> 如有需要先执行一次已有上下文压缩，再继续正常对话
```

### 指令加载

`instructions` 模块读取项目根、项目 `.okcode/` 目录和用户 `.okcode/` 目录的 `AGENTS.md`。项目根内容排在合并文本最前，其次是项目 `.okcode/`，最后是用户级内容。`@include` 只解析工作区相对目标，目标解析后必须仍在工作区根目录中；已访问集合和最大深度共同防止循环与无限嵌套。合并结果传给现有 `PromptOptionalSections.custom_instructions`，不改动稳定提示或 Provider 协议。

### 会话存档与恢复

`sessions` 模块负责协议无关的 `ChatMessage` JSONL 编解码、追加写、扫描会话摘要、30 天清理与容错恢复。每个日志行独立保存一条可恢复消息及其时间；扫描直接计算标题、消息数和最新时间。恢复跳过无法解析的行，并仅接受满足“助手工具调用后紧跟匹配工具结果”的有效前缀。

消息编解码同时保存 JSON 可表达的 `provider_state`。这是 Anthropic 重放“文本与工具调用共存”的助手消息所需的状态；OpenAI 重放时继续仅使用标准消息字段。普通启动创建会话 ID 但在首轮成功提交前不创建文件；用户立即恢复旧会话时，当前日志切换为选中日志，不会留下空文件。

### 长期记忆与后台处理

`memory` 模块将用户级和项目级笔记分别保存在各自 `memory/` 目录，每条笔记是带 frontmatter 的 Markdown。两个受限索引保存供模型读取的精简条目，并由 `MemoryStore` 在每次普通请求前读出、传入现有 `PromptOptionalSections.long_term_memory`。

`MemoryWorker` 使用单一后台线程和串行队列处理成功自然结束的 `MemoryJob`。线程拥有独立事件循环，并依据当前活动的 `ProviderConfig` 创建自己的 Provider 实例。这样不会与同步 REPL 使用的 `asyncio.Runner` 争用生命周期，也避免主循环在等待下一次输入时冻结记忆更新。后台失败只记录为内部失败结果，不反向影响前台会话。

### 会话与终端接入

`ConversationSession` 在完整提交 `pending` 消息后追加 JSONL，并投递包含本轮消息的记忆任务。恢复成功后替换内存消息、重建上下文管理器的用户原文记录，并为间隔较久的会话加入一次性系统提醒；下一次普通请求仍复用已有预算检测和压缩路径。

`OkCodeApp` 在调用 `stream_turn()` 前识别 `/resume`：它读取摘要、让 `TerminalUI` 显示表格并选择 ID，再运行恢复协程并渲染进度。此交互留在应用层，避免领域会话对象依赖 `prompt_toolkit`。

## 关键技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 自动笔记执行位置 | 独立后台线程与串行队列 | 主 REPL 的事件循环按单轮运行，普通协程任务会在等待输入时停住；单写者同时避免索引竞争。 |
| 自动笔记 Provider | 由当前 `ProviderConfig` 在 Worker 内新建实例 | 沿用同一模型和凭据配置，但不跨线程复用前台异步客户端。 |
| 会话持久化 | 一行一条协议无关消息的 JSONL | 追加成本低，损坏可定位到行，列表信息可直接扫描计算。 |
| 恢复完整性 | 只采用工具调用配对完整的消息前缀 | 防止 Provider 收到违反协议的不完整工具调用历史。 |
| Anthropic 重放 | 持久化 JSON 型 `provider_state` | 保留文本与 `tool_use` 混合内容，避免退化重建丢失文本。 |
| 新会话与恢复关系 | 新日志惰性创建，`/resume` 替换活动日志 | 默认始终是新会话，同时避免用户立即恢复时产生空存档。 |

## 核心数据结构与接口

### 指令加载

```python
InstructionPaths(root: Path, project: Path, user: Path)
InstructionLoader(paths: InstructionPaths, workspace_root: Path, max_include_depth: int = 5)

InstructionLoader.load() -> str
```

三条路径固定对应项目根、项目 `.okcode/` 和用户 `.okcode/` 的 `AGENTS.md`。`load()` 依序读取并展开每份文件；`@include` 使用工作区相对路径，递归调用携带 `visited: set[Path]` 和当前深度。缺失的顶层文件不报错，非法引用抛出带来源路径和行号的配置错误。

### 会话存档

```python
SessionConfig(retention_days: int = 30, long_gap: timedelta = timedelta(hours=24))
SessionDescriptor(id: str, title: str, message_count: int, updated_at: datetime)
RecoveredSession(
    messages: tuple[ChatMessage, ...],
    updated_at: datetime,
    skipped_lines: int,
    was_truncated: bool,
)

SessionJournal(session_id: str, path: Path)
SessionJournal.append(messages: Sequence[ChatMessage]) -> None

SessionStore(workspace_root: Path, config: SessionConfig | None = None)
SessionStore.create_journal() -> SessionJournal
SessionStore.list_resumable() -> tuple[SessionDescriptor, ...]
SessionStore.restore(session_id: str) -> RecoveredSession
SessionStore.cleanup_expired(now: datetime) -> int
```

每个 JSONL 行包含 UTC 时间戳和一条可恢复消息。消息编解码保存角色、文本、工具调用、工具结果和仅限 JSON 值的 `provider_state`。标题取第一条用户消息的单行摘要，消息数和更新时间由扫描结果计算。恢复跳过无法解析的行，并用工具调用 ID 集合验证“带工具调用的助手消息”与紧随的工具结果消息；一旦缺少、重复或错配，返回此前完整前缀并标记 `was_truncated`。

### 长期记忆

```python
MemoryScope = USER | PROJECT
MemoryCategory = PREFERENCE | CORRECTION | PROJECT_KNOWLEDGE | REFERENCE
MemoryJob(messages: tuple[ChatMessage, ...])
MemoryOperation(scope, category, action, note_ref, title, content, index_entry)
MemoryUpdate(operations, user_index, project_index)

MemoryPaths.for_workspace(workspace_root: Path) -> MemoryPaths
MemoryStore(paths: MemoryPaths, max_index_lines: int = 200, max_index_bytes: int = 25_000)
MemoryStore.read_context() -> str
MemoryStore.apply(update: MemoryUpdate) -> None

MemoryRequestFactory.build(job, user_index, project_index) -> ProviderRequest
MemoryRequestFactory.parse(response_text) -> MemoryUpdate
MemoryWorker(provider_factory, store, request_factory)
MemoryWorker.submit(job: MemoryJob) -> None
MemoryWorker.close() -> None
```

记忆统一位于项目内 `<项目>/.okcode/memory/`，其中项目笔记位于 `project/`，用户笔记位于 `user/`；两个目录各有自己的 Markdown 索引。每条笔记带有 `id`、范围、类别、创建时间和更新时间的 frontmatter。记忆请求携带本轮消息与两份既有索引，且始终没有工具、没有提示缓存。LLM 返回严格 JSON：操作只能是创建、向已有笔记追加内容或无变更，并包含两份完整候选索引；本地先校验所有字段、笔记引用、200 行和 25KB 限制，再以单文件原子写入更新笔记和索引。

`MemoryWorker` 是串行队列的单线程拥有者。它在自己的事件循环中创建和关闭 Provider，因此前台 Provider 不会跨线程使用；关闭时停止接收新任务，并以有限等待退出，不让后台故障阻塞程序退出。

### 会话接入接口

```python
ConversationSession.list_resumable_sessions() -> tuple[SessionDescriptor, ...]
ConversationSession.restore_session(session_id: str) -> AsyncIterator[TurnEvent]
```

`ConversationSession` 构造时新增可选 `SessionJournal`、`SessionStore`、`MemoryWorker` 和运行时提示词上下文来源。一次自然结束的最终回答提交后，先更新内存消息，再追加整个 `pending` 序列到 JSONL，最后投递 `MemoryJob`。恢复时替换内存历史，重新登记恢复历史中的用户原文到 `ContextManager`，并依据 `updated_at` 生成一次性的时间跨度系统提醒；接下来的正常请求走既有预算检查和压缩流程。

## 模块设计与文件组织

```text
src/okcode/
├── instructions.py
├── sessions/
│   ├── __init__.py
│   ├── models.py
│   ├── codec.py
│   └── store.py
├── memory/
│   ├── __init__.py
│   ├── models.py
│   ├── store.py
│   ├── request.py
│   └── worker.py
├── prompt/
│   └── runtime.py
├── context/manager.py
├── models.py
├── conversation.py
├── terminal.py
├── app.py
└── cli.py
```

### `instructions.py`

负责 `InstructionPaths` 和 `InstructionLoader`。只读取 UTF-8 Markdown，不创建或修改用户的指令文件。解析器仅将独占一行的 `@include <工作区相对路径>` 视作引用，递归展开时使用解析后的绝对路径检测环路，并拒绝符号链接解析后离开项目根目录的目标。

### `sessions/models.py`、`sessions/codec.py` 与 `sessions/store.py`

`models.py` 定义会话配置、列表摘要和恢复结果；`codec.py` 将 `ChatMessage`、`ToolCall`、`ToolExecutionResult` 和 JSON 型 `provider_state` 转为稳定 JSON 对象，并实现工具调用配对前缀校验；`store.py` 管理 `<项目>/sessions/<id>.jsonl`，提供惰性日志、扫描、恢复和 30 天清理。写入时逐条追加、刷新文件缓冲；进程中断后，恢复器只依赖可解析行而不信任文件尾部。

### `memory/models.py`、`memory/store.py`、`memory/request.py` 与 `memory/worker.py`

`models.py` 定义四种分类、双范围、工作任务和受控更新模型。`store.py` 管理 `<项目>/.okcode/memory/project/` 与 `<项目>/.okcode/memory/user/`：创建笔记时写入 YAML frontmatter，更新笔记时追加正文，所有单文件变更使用临时文件后替换；索引替换前检查所有索引条目能对应既有或同批新建笔记，并验证行数和 UTF-8 字节数。

`request.py` 构建无工具、无缓存的记忆请求。请求要求模型只输出可解析的 JSON，不得调用工具；解析器拒绝未知字段、无效枚举、跨范围引用和超出限制的候选索引。`worker.py` 以 `queue.Queue` 和 daemon 线程串行处理任务，在线程内部创建 Provider、消费到唯一的 `StreamCompleted`，解析并应用更新；任意 Provider、解析或写入错误均终止当前任务而不影响下一任务和前台会话。

### `prompt/runtime.py` 与 `context/manager.py`

`RuntimePromptContextFactory` 在 CLI 中接收工作区、已加载指令和 `MemoryStore`。每次被 `ConversationSession` 调用时读取两份当前索引，构造 `PromptBuildContext`，将指令传入 `custom_instructions`、将记忆传入 `long_term_memory`。`ContextManager` 新增恢复初始化方法，用恢复历史的所有用户文本重建 `original_user_messages`，并清空此前进程特有的摘要锚点与熔断状态。

### `conversation.py`、`app.py`、`terminal.py`、`models.py` 与 `cli.py`

`conversation.py` 在成功提交最终回答后执行“内存提交 -> JSONL 追加 -> MemoryJob 投递”，并新增 `list_resumable_sessions()` 与异步 `restore_session()`。恢复协程发出已有的 `AgentProgress` 供终端显示；恢复失败时发出新增的会话恢复停止原因，历史保持不变。时间跨度提醒以一次性动态系统补充进入下一次请求。

`app.py` 精确拦截 `/resume`：向会话查询列表，委托终端选择，取消时不修改当前会话，选中时运行恢复协程。`terminal.py` 用 Rich 表格显示 ID、标题、消息数和更新时间，并循环校验编号或取消输入。`cli.py` 启动时清理过期会话、创建惰性日志、加载指令、装配记忆 Store/Worker/运行时提示词来源；退出时先关闭 Worker，再关闭前台 Provider 和 MCP 连接。`models.py` 只在需要向终端表达恢复失败时增加新的 `AgentStopReason`。

## 模块交互

```text
CLI
  -> InstructionLoader / SessionStore / MemoryStore / MemoryWorker
  -> RuntimePromptContextFactory + ConversationSession
  -> OkCodeApp

ConversationSession --每轮请求--> RuntimePromptContextFactory --读取--> MemoryStore
ConversationSession --成功提交--> SessionJournal
ConversationSession --成功提交--> MemoryWorker --更新--> MemoryStore

OkCodeApp --/resume--> SessionStore 摘要列表 --> TerminalUI 选择
OkCodeApp --选中 ID--> ConversationSession.restore_session()
ConversationSession --恢复消息--> ContextManager
```

依赖始终由外向内：`sessions`、`memory` 和 `instructions` 不依赖终端、应用层或 `ConversationSession`；`MemoryWorker` 只依赖 Provider 工厂协议和 `MemoryStore`；`OkCodeApp` 只协调输入输出，不解析 JSONL 或笔记内容。

## 测试组织

| 文件 | 覆盖重点 |
| --- | --- |
| `tests/unit/test_instructions.py` | 三层优先级、递归引用、循环、深度、路径越界与缺失文件 |
| `tests/unit/test_sessions.py` | ID、JSONL 追加、扫描信息、坏行跳过、工具配对截断、过期清理、Provider 状态往返 |
| `tests/unit/test_memory_store.py` | frontmatter、双目录、原子更新、索引引用校验、200 行/25KB 上限 |
| `tests/unit/test_memory_request.py` | 无工具无缓存请求、四类笔记、严格 JSON、去重无变更与非法响应拒绝 |
| `tests/unit/test_memory_worker.py` | 后台串行、失败隔离、独立 Provider 创建与关闭 |
| 既有会话/应用/终端/CLI/上下文测试 | 提交后落盘、`/resume` 选择、时间提醒、恢复压缩、启动装配和回归 |
