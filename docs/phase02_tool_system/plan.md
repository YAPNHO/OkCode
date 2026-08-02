# OkCode 第二阶段：工具系统 Plan

## 架构概览

本阶段在现有 `ConversationSession` 与 Provider 抽象之间加入统一工具层。工具层仅描述和执行本地能力，不依赖 OpenAI 或 Anthropic；各 Provider 只负责将工具定义、流式工具调用和历史消息转换为所用协议的格式。

```text
用户输入
  ↓
ConversationSession
  ├─ 组装会话历史与本轮用户消息
  ├─ 向 LLMProvider 提供注册表中的工具定义
  └─ 处理文本回复或单次工具调用
  ↓
OpenAIProvider / AnthropicProvider
  ├─ 向模型声明工具
  ├─ 流式解析文本、思考或一次工具调用
  └─ 产出统一流事件
  ↓
ConversationSession
  ├─ 文本：原子提交用户消息和助手消息
  └─ 工具：执行 ToolExecutor，原子提交用户、调用和结果消息
  ↓
TerminalUI
  ├─ 展示思考和文本增量
  └─ 展示工具开始和完成摘要，然后恢复输入
```

模块职责：

| 模块 | 职责 |
|---|---|
| `okcode.tools` | 统一工具接口、元信息、结构化结果、注册中心、执行器、工作区边界和六个核心工具。它不依赖 Provider、会话或终端。 |
| `okcode.models` | 统一消息、工具调用、工具结果和会话可见事件。Provider 私有状态仍附着在统一消息上。 |
| `okcode.conversation` | 维护回合原子性和“工具后停止”的本章规则。它调用执行器，但不理解具体工具业务或协议格式。 |
| `okcode.providers.openai` | 映射 Chat Completions 工具声明；累积流中按索引到达的工具名称、调用 ID 与 JSON 参数片段；序列化 OpenAI 工具历史。 |
| `okcode.providers.anthropic` | 映射 Messages 工具声明；处理 `tool_use` 块与 `input_json` 参数分片；序列化 Anthropic 工具历史。 |
| `okcode.terminal` | 渲染文本/思考增量和工具状态摘要，不直接展示完整工具结果。 |
| `tests` | 覆盖工具、会话、Provider SSE 参数拼接和受控工作区端到端场景。 |

该分层使新增本地工具只改 `okcode.tools`，某个协议的格式变化只改相应 Provider；自动 Agent Loop 则可在下一阶段替换会话中的“工具后停止”策略，而不重写工具或 Provider。

## 核心数据结构

### JSONValue

```python
type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
```

所有工具 Schema、调用参数和结构化结果数据均限制为可 JSON 序列化的值，避免将文件句柄、异常对象或协议 SDK 对象写入会话历史。

### Role 与 ChatMessage

```python
class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolExecutionResult | None = None
    provider_state: object | None = field(default=None, repr=False, compare=False)
```

消息不变量由构造辅助函数和会话层保证：普通用户消息只有非空文本；普通助手消息有非空文本且没有 `tool_call`；工具调用助手消息恰有一个 `tool_call`；工具结果消息恰有一个 `tool_result`。`provider_state` 继续只存放生成消息的协议私有状态，例如 Anthropic 的原始内容块。

### ToolCall

```python
@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str
```

`arguments_json` 保留模型产生的完整原文。两个 Provider 先完成流式片段拼接，再由统一执行器解析并校验，JSON 不合法时转换为结果而非传播异常。

### ToolExecutionResult

```python
class ToolErrorCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_JSON = "invalid_json"
    INVALID_ARGUMENTS = "invalid_arguments"
    OUTSIDE_WORKSPACE = "outside_workspace"
    NOT_FOUND = "not_found"
    IO_ERROR = "io_error"
    MATCH_NOT_FOUND = "match_not_found"
    MATCH_NOT_UNIQUE = "match_not_unique"
    COMMAND_FAILED = "command_failed"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    tool_call_id: str
    tool_name: str
    success: bool
    content: str
    error_code: ToolErrorCode | None
    data: Mapping[str, JSONValue]
    truncated: bool = False
```

每个结果都会被稳定序列化为 JSON 文本回灌模型。失败结果保留 `success=false`、错误类别和可行动原因；命令非零退出也属于工具结果，不是 OkCode 自身的异常。

### ToolDefinition、Tool 与 ToolOutput

```python
@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, JSONValue]
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ToolOutput:
    content: str
    data: Mapping[str, JSONValue]


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    async def execute(self, arguments: Mapping[str, JSONValue]) -> ToolOutput: ...
```

`ToolDefinition` 是 Provider 生成 API 工具声明的唯一来源。具体工具返回 `ToolOutput`，统一执行器负责超时、截断、异常转换并封装为 `ToolExecutionResult`。

### ToolRegistry、ToolExecutor 与 Workspace

```python
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def definitions(self) -> tuple[ToolDefinition, ...]: ...


class ToolExecutor:
    async def execute(self, call: ToolCall) -> ToolExecutionResult: ...


class Workspace:
    @property
    def root(self) -> Path: ...
    def resolve_path(self, relative_path: str, *, must_exist: bool) -> Path: ...
```

注册表拒绝重名，并以稳定顺序返回定义。执行器按顺序执行查找工具、JSON 解析、JSON Schema 校验、超时控制、结果截断与异常封装。`Workspace` 在启动时固定根目录；它拒绝绝对路径和包含父级回退的输入，并验证解析后的文件或目录没有经符号链接离开根目录。

### 会话与显示事件

```python
@dataclass(frozen=True, slots=True)
class ToolExecutionStarted:
    tool_name: str


@dataclass(frozen=True, slots=True)
class ToolExecutionFinished:
    result: ToolExecutionResult


type TurnEvent = ThinkingDelta | TextDelta | ToolExecutionStarted | ToolExecutionFinished
```

Provider 的 `StreamCompleted` 始终携带最终 assistant 消息：它可以是普通文本消息，也可以是带一个 `ToolCall` 的工具调用消息。`ConversationSession.stream_turn()` 只向 UI 产出 `TurnEvent`，内部的完整工具结果不会直接倾倒到终端。

## 工具设计

### 核心工具与输入 Schema

| 工具名 | 必填参数 | 可选参数 | 成功结果 |
|---|---|---|---|
| `read_file` | `path` | 无 | 相对路径和文本内容。 |
| `write_file` | `path`、`content` | 无 | 相对路径、写入字符数、创建父目录数量。 |
| `edit_file` | `path`、`old_text`、`new_text` | 无 | 相对路径和替换次数；只允许一次。 |
| `run_command` | `command` | 无 | 退出状态、标准输出、标准错误。 |
| `find_files` | `pattern` | `path`，默认根目录 | 稳定排序的相对文件路径。 |
| `search_code` | `query` | `path`、`pattern` | 命中的相对路径、行号和行文本。 |

每个 Schema 均声明对象类型、必填字段与 `additionalProperties: false`。`jsonschema` 负责在工具运行前验证 JSON 参数；验证失败产生 `invalid_arguments` 结果。

### 文件工具

- 文本文件以 UTF-8 读取和写入；文件不存在、目标为目录、解码失败和 IO 异常转换为结构化结果。
- `read_file` 采用有界读取，超过工具结果上限时返回前段文本并设置 `truncated=true`。
- `write_file` 在工作区内创建缺失父目录。它先把完整内容写入目标同目录临时文件，写入成功后通过原子替换提交；失败时清理临时文件。
- `edit_file` 在任何写入前统计 `old_text` 的出现次数。出现 `0` 次返回 `match_not_found`，超过 `1` 次返回 `match_not_unique`；两种情况均不修改原文件。恰为 `1` 次时通过同目录临时文件与原子替换提交新内容。
- 工具接受的路径一律为工作区相对路径；绝对路径、路径回退、符号链接越界和最终解析后越界均返回 `outside_workspace`，且不泄漏外部文件内容。

### 查找与搜索工具

- `find_files` 使用标准库路径匹配遍历工作区，支持例如 `**/*.py` 的模式；可选 `path` 必须为工作区内目录。候选项经解析后越界的符号链接不返回，结果使用相对根目录的路径并稳定排序。
- `search_code` 在工作区内逐行检索 UTF-8 文本；可选 `path` 限定起始目录，`pattern` 限定候选文件。每条命中包含 `path`、`line_number` 和 `line`。无法解码的文件及越界符号链接跳过。
- 返回的文件数量、命中数量和单行长度均受统一结果上限约束；达到上限时停止继续收集，并将 `truncated=true` 写入结果。

### 命令工具

- `run_command` 使用当前操作系统的 shell 运行命令，固定 `cwd` 为工作区根目录。它不提供越过工作区的文件工具访问能力，也不承诺对命令本身进行操作系统级隔离。
- 进程的标准输出和标准错误并发、有界读取，达到结果上限后继续排空管道而丢弃超出内容，避免大输出阻塞子进程或填满会话上下文。
- 子进程以独立进程组运行。超时时，Windows 终止进程树，类 Unix 系统终止进程组并等待回收；返回 `timeout` 结果。非零退出保留退出码和已捕获输出，返回 `command_failed` 结果。

## Provider、会话与终端设计

### LLMProvider

```python
class LLMProvider(Protocol):
    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
    ) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None: ...
```

会话把注册表的工具定义传给 Provider。工具层不会感知协议，Provider 不调用工具、不执行业务参数校验，也不决定是否自动继续下一次模型调用。

### OpenAIProvider

- 将每个定义映射为 `{"type": "function", "function": {"name", "description", "parameters"}}`，并在请求中设置 `parallel_tool_calls=False`。
- 流中以工具调用索引为键累积 `id`、函数 `name` 和 `arguments` 片段；只有确认结束后才构建一个 `ToolCall`。
- 允许普通文本与一个工具调用同一响应出现；没有工具调用时保持第一阶段的文本完成语义。
- 发现多个工具调用、缺失调用 ID/名称或流完成前参数不完整时，报告 Provider 流错误，不执行、不提交本轮历史。
- 后续请求把内部工具调用消息序列化为 assistant `tool_calls`，把工具结果消息序列化为 `role="tool"`、匹配 `tool_call_id` 的 JSON 内容。

### AnthropicProvider

- 将定义映射为 `{"name", "description", "input_schema"}`，并请求 `tool_choice={"type": "auto", "disable_parallel_tool_use": True}`。
- 在 `content_block_start` 识别 `tool_use` 内容块；从 SDK 的 `input_json` 事件累积 `partial_json`，并以最终块中的 ID、名称和输入构建 `ToolCall`。
- 助手的最终原始内容块保存到 `provider_state`，保证后续请求能够完整重放 `tool_use`、thinking 签名等协议状态。
- 发现多个 `tool_use` 块、缺失 ID/名称、无效/未完成 JSON 或 `stop_reason` 与内容不一致时，报告 Provider 流错误，不执行、不提交本轮历史。
- 后续请求保留 assistant 原始 `tool_use` 块，并将工具结果序列化为紧邻的 user `tool_result` 内容块；失败结果设置 `is_error=true`。

### ConversationSession

```text
普通文本回合：
  历史 + 用户消息 → Provider → assistant 文本消息
  → 原子提交 [用户, assistant]

单次工具回合：
  历史 + 用户消息 → Provider → assistant 工具调用消息
  → 产出 ToolExecutionStarted
  → ToolExecutor.execute(call)
  → 产出 ToolExecutionFinished
  → 原子提交 [用户, assistant 工具调用, tool 结果]
  → 结束本轮，不再次请求模型
```

会话拒绝同时具有文本回答与多个调用、多个完成事件、完成后额外增量、没有完成事件或不满足消息不变量的 Provider 流。只要模型产生的调用本身合法，工具成功或失败都会成对提交调用消息和结果消息，保证下一轮历史有效；Provider 流错误或用户中断则维持整轮回滚。

### TerminalUI 与 CLI 装配

- 终端对 `ToolExecutionStarted` 显示工具名称和“正在执行”状态。
- 对 `ToolExecutionFinished` 只显示成功/失败、工具名称和短摘要；详细 JSON 结果仅进入会话历史。
- CLI 在启动时以当前工作目录创建 `Workspace`、默认六工具注册表和 `ToolExecutor`，再将执行器注入 `ConversationSession`。
- 普通文本流式显示、取消、错误和退出行为保持第一阶段兼容。

## 模块交互

```text
build_default_registry(Workspace)
  → ToolRegistry
  → ToolExecutor
  → ConversationSession(provider, executor)

ConversationSession
  → registry.definitions()
  → provider.stream(history_plus_user, definitions)
  → StreamCompleted(assistant tool call)
  → executor.execute(call)
  → internal tool message
  → history commit

下一轮 provider.stream(history, definitions)
  → OpenAI 序列化 assistant.tool_calls + role=tool
  或 Anthropic 序列化 tool_use + tool_result
```

依赖方向保持单向：`tools` 和 `models` 位于底层；`providers` 依赖 `models` 与工具定义；`conversation` 依赖 Provider 抽象和工具执行器；`terminal` 只依赖显示事件；`cli` 负责组合它们。不存在 Provider 到工具实现、工具到终端或工具到会话的反向依赖。

## 文件组织

```text
src/okcode/
├── models.py                         # 扩展消息、工具调用、结果和回合事件
├── conversation.py                   # 工具执行、原子提交、工具后停止
├── app.py                            # 渲染 TurnEvent
├── cli.py                            # 工作区、注册表、执行器装配
├── terminal.py                       # 工具状态摘要渲染
├── providers/
│   ├── base.py                       # stream(messages, tools) 接口
│   ├── openai.py                     # OpenAI 工具声明、SSE 拼接、历史序列化
│   └── anthropic.py                  # Anthropic 工具声明、SSE 拼接、历史序列化
└── tools/
    ├── __init__.py                   # 默认注册表工厂导出
    ├── models.py                     # ToolDefinition、结果、错误码、输出限制
    ├── base.py                       # Tool Protocol
    ├── registry.py                   # 集中登记与查询
    ├── executor.py                   # JSON/Schema/超时/异常/截断统一处理
    ├── workspace.py                  # 工作区路径解析和越界保护
    ├── files.py                      # read_file、write_file、edit_file
    ├── command.py                    # run_command
    ├── search.py                     # find_files、search_code
    └── defaults.py                   # 六个核心工具的装配

tests/
├── fakes.py                          # 扩展假 Provider、假工具和假终端
├── unit/
│   ├── test_models.py                # 消息不变量与结果序列化
│   ├── test_conversation.py          # 普通/工具回合原子提交与停止行为
│   ├── test_terminal.py              # 工具状态摘要渲染
│   ├── test_tools_registry.py        # 登记、重名、查询和声明
│   ├── test_tools_executor.py        # JSON、Schema、超时、截断和异常结果
│   ├── test_tools_workspace.py       # 工作区、回退路径和符号链接越界
│   ├── test_tools_files.py           # 读写与唯一替换原子性
│   ├── test_tools_command.py         # 非零退出、超时和有界输出
│   └── test_tools_search.py          # 模式查找、代码行匹配和截断
└── integration/
    ├── test_openai_sse.py            # OpenAI 工具参数片段和历史序列化
    ├── test_anthropic_sse.py         # Anthropic input_json 片段和历史序列化
    └── test_tool_turn.py             # 受控工作区端到端单工具回合

pyproject.toml                         # 添加 jsonschema 运行依赖
uv.lock                                # 锁定新增依赖
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 工具协议 | `ToolDefinition` + `Tool` Protocol + `ToolExecutor` | 将工具描述、业务执行和错误治理分开，新增工具无需修改 Provider。 |
| 参数校验 | `jsonschema`，每个 Schema 禁止额外字段 | 模型工具参数天然是 JSON，使用标准 Schema 可同时服务 API 声明与运行时校验。 |
| 内部历史 | `Role.TOOL` + `ToolCall` + `ToolExecutionResult` | 使用一套会话模型表达两种协议要求，避免会话层耦合 API 格式。 |
| 协议回灌 | Provider 持有序列化职责 | OpenAI 和 Anthropic 的调用/结果消息形态不同，放在边界处隔离。 |
| 单调用限制 | 请求参数限制 + Provider 防御校验 | SDK 设置减少模型产生多调用的概率；运行时校验确保本章不越界。 |
| 文件操作 | UTF-8 + 同目录临时文件 + 原子替换 | 使编辑的唯一匹配语义可靠，避免半写入。 |
| 工作区保护 | 解析后的路径包含关系检查 | 同时阻止 `..`、绝对路径和符号链接越界。 |
| 搜索与找文件 | Python 标准库实现 | 不依赖是否安装 `rg` 或平台特定命令，测试与跨平台行为可控。 |
| 命令执行 | 平台 shell + 独立进程组 + 有界并发读取 | 保留用户期望的 shell 语义，同时保证超时可回收、输出不失控。 |
| 输出限制 | 执行器统一截断，并保留 `truncated` 标志 | 每个工具都不会因为大输出耗尽模型上下文。 |
| 本章停止点 | 会话提交工具结果后返回 UI | 精确满足单次调用要求，为下一章 Agent Loop 留出明确扩展点。 |

## Spec 覆盖

| Spec 需求 | 设计归属 |
|---|---|
| F1、F3 | `ToolDefinition`、`Tool`、`ToolRegistry` 与 Provider 声明映射。 |
| F2、F4 | 六个工具、`Workspace`、文件与搜索工具设计。 |
| F5、F6 | `ToolExecutor`、`ToolExecutionResult`、超时和有界输出。 |
| F7、F8 | OpenAI/Anthropic Provider 的流式拼接和历史序列化。 |
| F9、F10 | `ConversationSession` 的单次调用状态机和三消息原子提交。 |
| N1-N7 | Schema 校验、结构化错误、原子写入、路径保护、协议边界及分层测试设计。 |
