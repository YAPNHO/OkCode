# OkCode 第七阶段：上下文管理 Plan

## 架构概览

本阶段采用“会话编排与上下文管理分离”的结构：`ConversationSession` 仍然拥有会话历史、Agent Loop 和 Provider 调用；新的 `context` 包只处理结果外置、预算估算、摘要候选、摘要状态和熔断。这样工具调用与工具结果的协议配对继续由原会话历史维护，不会因压缩而被破坏。

```text
工具执行结果
  -> ContextManager 轻量外置
  -> ConversationSession 待提交历史
  -> ContextManager 估算完整正常请求
  -> 未超过 167K：Provider 正常请求，记录 Usage 锚点
  -> 超过 167K 或 /compact：构造无工具摘要请求
  -> 解析并提交摘要状态与边界系统补充
  -> 重新构造正常请求
```

重量压缩只从已完成历史中选择可摘要前缀；当前轮的 `pending` 消息保持原样，尤其不拆分助手工具调用和其后的工具结果。摘要与边界消息进入每次正常请求的动态系统补充，不伪造普通聊天消息，因此 OpenAI 与 Anthropic 现有的序列化逻辑不需要接受新的消息角色或不合法的工具配对。

## 核心数据结构与接口

### `ContextConfig`

```python
ContextConfig(
    context_window_tokens=200_000,
    automatic_compaction_tokens=167_000,
    summary_output_reserve_tokens=20_000,
    safety_margin_tokens=13_000,
    chars_per_token=4,
    max_tool_result_chars=50_000,
    max_tool_message_chars=200_000,
    retain_recent_tokens=10_000,
    retain_recent_messages=5,
    summary_failure_limit=3,
)
```

该配置为进程内固定默认值，不新增用户配置文件字段。`20_000` 仅参与输入阈值计算，不改变 Provider 的输出参数。

### `TokenEstimateAnchor` 与 `ConversationContextState`

```python
TokenEstimateAnchor(input_tokens: int, input_chars: int)

ConversationContextState(
    summary: str | None,
    boundary_message: str | None,
    original_user_messages: tuple[str, ...],
    estimate_anchor: TokenEstimateAnchor | None,
    consecutive_summary_failures: int,
    circuit_open: bool,
)
```

`estimate_anchor` 保存最近一次正常模型请求的真实输入 Token 与完整请求字符数。后续请求估算为“锚点 Token + 当前字符数相对锚点的差值除以四”；没有可用 Usage 时使用当前完整请求字符数除以四。

`original_user_messages` 是摘要的确定性数据源：每条进入会话的用户原文按顺序保存。正式摘要的“所有用户消息”部分由程序写入，而不是依赖模型转述，从而保证逐字保留，并可在多次摘要后继续保留早期原文。

### 外置与摘要计划模型

```python
ToolResultArtifact(relative_path: str, original_chars: int)

SummaryPlan(
    history_to_summarize: tuple[ChatMessage, ...],
    retained_history: tuple[ChatMessage, ...],
    transcript: str,
    original_user_messages: tuple[str, ...],
)
```

`ToolResultArtifact` 指向完整稳定序列化结果文件。替换后的 `ToolExecutionResult` 保留工具调用 ID、工具名、成功状态和错误码，仅使用简短预览和结构化路径元信息代替原内容与原数据。

`SummaryPlan` 的保留尾部至少满足“约 10K Token”和“至少 5 条消息”两项下限中的较大者，并向前扩展到安全的已完成轮次边界。`transcript` 包含旧正式摘要和待摘要历史；所有用户原文由单独字段提供。

### 核心接口

```python
class ArtifactStore:
    def externalize(self, result: ToolExecutionResult, ordinal: int) -> ToolResultArtifact: ...


class ContextManager:
    def normalize_tool_results(
        self, results: Sequence[ToolExecutionResult]
    ) -> tuple[ToolExecutionResult, ...]: ...
    def estimate_input(self, request: ProviderRequest) -> int: ...
    def needs_automatic_compaction(self, request: ProviderRequest) -> bool: ...
    def plan_compaction(
        self,
        committed: Sequence[ChatMessage],
        pending: Sequence[ChatMessage],
    ) -> SummaryPlan | None: ...
    def commit_summary(self, plan: SummaryPlan, summary: str) -> tuple[ChatMessage, ...]: ...
    def record_normal_usage(self, request: ProviderRequest, usage: TokenUsage) -> None: ...
    def record_summary_failure(self) -> bool: ...
    def reset_summary_failures(self) -> None: ...
    def system_instructions(self) -> tuple[SystemInstruction, ...]: ...


class SummaryRequestFactory:
    def build(self, plan: SummaryPlan) -> ProviderRequest: ...
    def extract_final_summary(self, response_text: str) -> str: ...
```

`record_summary_failure()` 返回熔断是否刚刚打开。`commit_summary()` 只在正式摘要校验通过后更新状态并返回新的已完成历史，保证调用方能够原子替换。

## 模块设计

### `context/artifacts.py`

**职责：** 将完整 `ToolExecutionResult.to_json()` 内容写入 `.okcode/context/<会话标识>/tool-results/`，生成工作区相对路径和预览替换结果。

**对外接口：** `ArtifactStore.externalize()`。

**依赖：** `Path`、`ToolExecutionResult` 和上下文数据模型。

文件以临时文件写入后原子重命名。一次轻量压缩的所有目标文件都写入成功后，调用方才提交替换后的工具消息；失败时历史不变，遗留的未引用文件不进入历史，且不属于本阶段的自动清理范围。

### `context/manager.py`

**职责：** 管理会话级上下文状态，计算字符与 Token 估算，执行 `50K/200K` 轻量选择，计算安全的重量压缩边界，并维护摘要失败计数。

**对外接口：** `normalize_tool_results()`、`estimate_input()`、`needs_automatic_compaction()`、`plan_compaction()`、`commit_summary()`、`record_normal_usage()` 和熔断相关方法。

**依赖：** 上下文模型、`ArtifactStore`、领域消息模型、工具结果模型与提示系统指令类型；不依赖 Provider 或 Agent Loop。

单个工具结果和单条工具消息总量均按完整稳定序列化文本的字符数判断。总量超限时，管理器以原始字符数降序选取外置目标，直到替换后的合计不超过 `200_000` 字符；同长度时保持原始结果顺序。

### `context/summary.py`

**职责：** 将旧摘要、待摘要消息和用户原文转换为摘要输入，构造专用系统提示和无工具 `ProviderRequest`，并从模型返回文本中提取正式摘要。

**对外接口：** `SummaryRequestFactory.build()` 与 `extract_final_summary()`。

**依赖：** `ChatMessage`、`ProviderRequest`、`PromptBundle`、`PromptCachePolicy` 和上下文模型。

专用提示明确禁止工具调用，并要求模型输出内部分析草稿与正式摘要两个带标记区域。解析器丢弃分析草稿，仅接受正式区域；正式区域必须包含九个规定标题和“所有用户消息”占位标记。解析后由程序将 `original_user_messages` 原样填入该部分；缺少标记、标题不完整、存在工具调用或无正式文本均视为本次摘要失败。摘要请求不启用缓存，也不进入正常 Agent Loop。

### `conversation.py`

**职责：** 在现有 Agent Loop 中调用上下文管理能力，处理 `/compact`，调用摘要 Provider，并按成功或失败结果更新会话历史。

**对外接口：** 现有 `ConversationSession.stream_turn()` 保持不变；构造函数新增可选 `ContextManager` 依赖。

**依赖：** `ContextManager`、`SummaryRequestFactory`、既有 Provider、工具执行器、提示构建器和领域事件。

工具执行完成后先调用 `normalize_tool_results()`，再将工具消息追加到 `pending`。构造每个正常请求后调用 `needs_automatic_compaction()`；需要压缩时先发出 `AgentProgress`，调用摘要请求并在成功后更新历史、重建正常请求。摘要失败时发出新的上下文停止原因并结束当前轮，不继续发送可能超窗的正常请求。

`/compact` 直接执行同一摘要流程，不创建普通用户消息，也不进入 Agent Loop。没有可摘要历史时仅发出无操作状态。会话熔断后，自动和手动摘要均发出熔断停止原因，不再调用 Provider。

### `prompt/builder.py`

**职责：** 接收上下文管理模块提供的动态系统补充，并与环境、可选段和任务模式说明按优先级合并。

**对外接口：** `PromptBuildContext` 新增默认空的额外系统补充字段；`PromptBuilder.build()` 将其纳入动态提示。

**依赖：** 既有提示模型。

摘要和边界信息将被包装为明确的引用记录，声明其中内容不能覆盖现有系统约束，也不能替代重新读取文件。它们不进入稳定缓存前缀，确保每次压缩后立即生效。

### `cli.py` 与顶层 `models.py`

**职责：** CLI 使用当前 `Workspace` 根目录创建会话隔离的 `ArtifactStore` 和 `ContextManager` 后注入 `ConversationSession`。顶层模型新增上下文压缩失败与熔断的 `AgentStopReason`，继续复用 `TerminalUI` 对 `AgentStopped` 的渲染。

**依赖：** 既有 Workspace、会话和领域模型。

## 模块交互

### 正常请求

```text
ConversationSession
  -> 执行工具
  -> ContextManager.normalize_tool_results
  -> 将定型 Tool 消息加入 pending
  -> 构建含摘要/边界系统补充的 ProviderRequest
  -> ContextManager.estimate_input
  -> 未达 167K：Provider.stream
  -> StreamCompleted：ContextManager.record_normal_usage
```

### 自动全量摘要

```text
ConversationSession
  -> 构建正常 ProviderRequest 并估算
  -> 达到 167K：ContextManager.plan_compaction
  -> SummaryRequestFactory.build（tools 为空、缓存关闭）
  -> Provider.stream（不向终端转发 ThinkingDelta/TextDelta）
  -> SummaryRequestFactory.extract_final_summary
  -> ContextManager.commit_summary
  -> 重建正常 ProviderRequest 并继续本轮
```

自动摘要的候选只削减已完成历史，`pending` 仍随本轮正常请求发送。若在保留 `pending` 和最小尾部后已无可摘要前缀，当前轮以上下文压缩失败停止，而不删减用户原文或破坏工具协议。

### 手动压缩与失败

```text
/compact
  -> 存在可摘要历史：计划、摘要、校验、原子提交
  -> 不存在可摘要历史：显示无操作状态
  -> 摘要失败：历史不变，失败计数加一，停止当前命令
  -> 第三次连续失败：打开会话熔断
  -> 熔断后：不再调用摘要 Provider，显示熔断原因
```

成功摘要会重置连续失败计数。失败不会产生半替换历史；下一次用户发起的新请求才会形成下一次独立摘要尝试，不在同一轮中自动重试。

## 文件组织与测试设计

```text
src/okcode/
├── context/
│   ├── __init__.py
│   ├── models.py
│   ├── artifacts.py
│   ├── manager.py
│   └── summary.py
├── conversation.py
├── models.py
├── prompt/builder.py
└── cli.py

tests/
├── unit/test_context_artifacts.py
├── unit/test_context_manager.py
├── unit/test_context_summary.py
├── unit/test_conversation.py
├── unit/test_prompt_builder.py
└── integration/test_tool_turn.py
```

- `test_context_artifacts.py`：验证 `50_000` 字符边界、原子写入、相对路径、预览和完整 JSON 还原。
- `test_context_manager.py`：验证 `200_000` 字符聚合选择、相同大小稳定顺序、Usage 锚点估算、`167_000` Token 阈值、尾部保留与三次熔断。
- `test_context_summary.py`：验证无工具请求、禁止工具提示、草稿丢弃、九段标题、原样用户消息占位替换和非法摘要拒绝。
- `test_conversation.py`：验证自动摘要在正常请求前发生、手动 `/compact` 在低预算下仍执行、失败不提交历史、熔断停止和 Usage 锚点更新。
- `test_prompt_builder.py`：验证摘要和边界作为动态系统补充注入，不进入稳定缓存前缀。
- `test_tool_turn.py`：用真实工具执行链验证大结果在进入下一次模型请求前已被替换为可读路径。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 工具结果计数 | 完整 `ToolExecutionResult.to_json()` 的字符数 | `content` 和结构化 `data` 都会进入 Provider 输入。 |
| 外置目录 | `.okcode/context/<会话标识>/` | 在工作区沙箱内且可被读取工具访问；现有 `.gitignore` 已排除 `.okcode/*`。 |
| Token 估算 | Usage 锚点加字符增量，4 字符约等于 1 Token | 满足近似计量约束，并与 `50K` 字符约等于 `12.5K` Token 一致。 |
| 自动阈值 | `167K = 200K - 20K - 13K` | 分别预留摘要输出容量与估算误差。 |
| 用户原文保留 | 程序维护原文列表并填充摘要第六段 | 不能依赖 LLM 转述来保证逐字保留。 |
| 摘要载体 | 动态系统补充而非聊天消息 | 不改变双 Provider 的聊天角色与工具结果配对。 |
| 摘要草稿 | 带标记返回后由解析器丢弃 | 模型可先推理，持久上下文只保存正式摘要。 |
| 手动命令 | `/compact` | 与既有斜杠命令一致，且无条件发起压缩。 |
| Provider 参数 | 不修改现有模型参数或协议 | `20K` 是预算预留，不是新的 Provider 输出配置。 |
| 失败处理 | 新增 `AgentStopped` 原因 | 复用既有终端展示并阻止超窗正常请求。 |
