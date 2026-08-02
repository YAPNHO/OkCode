# OkCode 第三阶段：Agent Loop Plan

## 架构概览

本阶段在第二阶段工具系统之上加入一个会话级 Agent 运行时。整体保持“Provider 只负责协议适配、工具系统只负责执行、终端只负责展示”的边界，把自主循环、停止条件、计划模式和多工具调度集中在会话运行层。

核心数据流如下：

1. 终端读取用户输入，把原始文本交给会话层。
2. 会话层识别普通任务、`/plan` 或 `/do`，生成本轮运行请求。
3. Agent Loop 使用当前已提交历史加本轮临时消息调用 Provider。
4. Provider 流式产出文本增量，并在结束时返回完整助手消息、工具调用列表和 Token 用量。
5. Agent Loop 将文本、进度、用量、工具调用和工具结果都转换为统一事件给界面。
6. 如果助手消息包含工具调用，工具调度器按安全类别分批执行并把结果写入临时上下文，然后继续下一轮模型调用。
7. 如果助手消息给出最终文本，则一次性提交本轮临时上下文；其他停止路径不提交历史。

## 核心数据结构

### `ToolCall`

表示模型请求的一次工具调用，继续包含：

- `id`：Provider 返回的调用标识，用于后续工具结果对应。
- `name`：工具名称。
- `arguments_json`：完整 JSON 参数文本。

### `ChatMessage`

会话消息从“单工具调用/单工具结果”扩展为“多工具调用/多工具结果”：

- 用户消息：只包含用户文本。
- 助手消息：包含正式文本和零个或多个工具调用。
- 工具消息：包含一个或多个工具执行结果。
- Provider 私有状态继续保留在消息内，只由生成它的 Provider 解释。

设计理由：一次助手回复可以同时产生多个工具调用，后续必须把所有调用及其结果一一回灌。工具消息支持多个结果后，Anthropic 可以序列化为同一个用户消息中的多个 `tool_result` 块，OpenAI 可以展开为多个 `tool` 消息。

### `TokenUsage`

表示一次模型请求的 Token 用量：

- `input_tokens`：输入 Token 数；不可用时为空。
- `output_tokens`：输出 Token 数；不可用时为空。
- `total_tokens`：总 Token 数；不可用时为空。
- `available`：是否来自 Provider 的真实用量信息。

Provider 无法提供精确数据时，事件仍然存在，但 `available=false`，不做估算。

### `ToolSafety`

工具安全类别放在工具定义里：

- `read_only`：不修改工作区、不执行命令，可并发执行。
- `side_effect`：可能修改文件、执行命令或产生外部副作用，必须串行执行。

当前六个工具的分类：

| 工具 | 类别 | 理由 |
|---|---|---|
| `read_file` | `read_only` | 只读取文本文件 |
| `find_files` | `read_only` | 只遍历工作区路径 |
| `search_code` | `read_only` | 只读取并搜索文本内容 |
| `write_file` | `side_effect` | 会创建或覆盖文件 |
| `edit_file` | `side_effect` | 会修改文件内容 |
| `run_command` | `side_effect` | 命令可能产生任意外部副作用 |

### Agent 事件

事件层覆盖界面可见状态和运行时状态：

- `ThinkingDelta`：思考文本增量。
- `TextDelta`：正式回答文本增量。
- `AgentProgress`：模型迭代、工具批次、停止原因等进度信息。
- `ToolCallRequested`：模型请求的单个工具调用。
- `ToolExecutionStarted`：工具开始执行。
- `ToolExecutionFinished`：工具执行完成。
- `TokenUsageReported`：一轮模型请求结束后的 Token 用量。
- `AgentStopped`：达到迭代上限、连续未知工具或没有可执行计划等非成功结束状态。

终端可以选择只展示其中一部分，但 Agent Loop 不直接调用终端方法。

### `AgentConfig`

保存循环安全参数：

- `max_iterations=12`。
- `unknown_tool_limit=2`。

后续如果要做配置文件扩展，只需要从配置层注入该结构，本阶段先使用默认值。

### `SavedPlan`

会话内最近一次成功 `/plan` 的结果：

- `task`：用户在 `/plan` 后输入的原始任务。
- `content`：模型最终产出的计划文本。

它只存在于当前进程会话内，不写入磁盘。

## 核心接口

### Provider 接口

Provider 继续暴露异步流式接口，但结束事件需要携带多工具调用和 Token 用量：

- 输入：会话消息序列、当前可见工具定义。
- 输出：文本增量、思考增量、流完成事件。
- 流完成事件中的助手消息可以包含多个工具调用。
- 流完成事件同时携带 `TokenUsage`，没有精确用量时携带不可用状态。

OpenAI 适配器需要把流中的多个 `tool_calls` 按 index 分别累计，并在结束时按 index 排序生成工具调用列表。请求层不再显式关闭并行工具调用。

Anthropic 适配器需要保留最终消息里的所有 `tool_use` 块，按内容顺序生成工具调用列表。请求层不再禁止模型返回多个工具使用块。

### 工具注册表接口

工具注册表继续负责集中管理工具，并新增按类别过滤定义的能力：

- 获取全量工具定义：普通任务和 `/do` 使用。
- 获取只读工具定义：`/plan` 使用。
- 按名称查询工具：执行器继续使用。

工具注册表不负责执行，也不参与 Plan Mode 判断。

### Agent Loop 接口

Agent Loop 接收一次运行请求并输出事件流：

- 普通任务：使用用户原文和全量工具。
- `/plan`：使用用户任务文本和只读工具，成功后保存计划。
- `/do`：使用最近保存计划生成执行任务，并暴露全量工具。

Agent Loop 内部维护本轮临时上下文、迭代计数、连续未知工具计数和待提交消息。只有成功得到最终文本时，才通知会话层提交。

### 会话接口

会话层继续对外提供“输入文本 -> 事件流”的入口：

- 保存已提交历史。
- 保存当前会话最近计划。
- 识别 `/plan` 和 `/do`。
- 在 Agent Loop 成功结束时提交本轮历史。
- 在取消、异常或停止时丢弃本轮临时上下文。

### 终端接口

终端继续只消费事件：

- 文本增量沿用现有展示。
- 工具开始/结束沿用现有摘要展示。
- 新增进度、用量和停止事件的轻量展示。
- `/do` 无计划时展示明确提示，不显示 Provider 错误。

## 模块设计

### `src/okcode/models.py`

职责：保存协议无关的对话消息和事件模型。

主要改动：

- 将助手消息从单个工具调用扩展为工具调用元组。
- 将工具消息从单个工具结果扩展为工具结果元组。
- 新增 Token 用量、进度、工具调用请求和停止事件。
- 保留现有文本增量事件，减少终端层改动。

### `src/okcode/conversation.py`

职责：会话状态、Plan Mode 状态和 Agent Loop 编排。

主要改动：

- 用循环替代当前“一次 Provider 请求 + 最多一次工具调用”的流程。
- 在每次迭代结束后处理 Token 用量事件。
- 根据工具调用结果更新连续未知工具计数。
- 成功最终文本时提交完整本轮历史；其他停止路径不提交。
- 解析 `/plan`、`/do`，并维护当前会话最近计划。

### `src/okcode/tools/models.py`

职责：工具定义和执行结果结构。

主要改动：

- 新增工具安全类别。
- 工具定义携带安全类别，默认值选择有副作用类别，避免新工具被误并发。

### `src/okcode/tools/registry.py`

职责：工具注册、查询和定义过滤。

主要改动：

- 支持按安全类别返回工具定义。
- 保持工具名称排序，确保测试和模型请求稳定。

### `src/okcode/tools/files.py`、`src/okcode/tools/search.py`、`src/okcode/tools/command.py`

职责：六个核心工具声明自身安全类别。

主要改动：

- `read_file`、`find_files`、`search_code` 标记为只读。
- `write_file`、`edit_file`、`run_command` 标记为有副作用。

### `src/okcode/tools/executor.py`

职责：单个工具的参数校验、超时、异常转换和结构化结果。

主要改动：

- 保持单工具执行入口不变。
- Agent Loop 负责并发批次；执行器只保证每个工具调用安全地变成结果。

### `src/okcode/providers/openai.py`

职责：OpenAI Chat Completions 流式协议适配。

主要改动：

- 累计并返回多个工具调用。
- 序列化助手消息中的多个工具调用。
- 序列化工具消息时展开多个工具结果。
- 请求工具时允许模型返回多个工具调用。
- 尽量读取官方流式用量；不可用时返回不可用用量事件。

### `src/okcode/providers/anthropic.py`

职责：Anthropic Messages 流式协议适配。

主要改动：

- 累计并返回多个 `tool_use` 块。
- 保存包含多个工具使用块的 Provider 私有状态。
- 序列化工具消息时生成多个 `tool_result` 块。
- 请求工具时允许模型返回多个工具使用块。
- 聚合 message start / delta 中可得的 Token 用量。

### `src/okcode/app.py`

职责：同步 REPL 和异步事件消费。

主要改动：

- 继续把非退出输入交给会话层。
- 对取消保持现有行为。
- 流正常结束后统一调用终端结束本轮。

### `src/okcode/terminal.py`

职责：事件展示。

主要改动：

- 新增工具调用请求、进度、Token 用量和停止事件的渲染。
- 保持文本、思考和工具结果展示风格不变。

## 模块交互

### 普通 Agent Loop

```text
用户输入
  -> OkCodeApp
  -> ConversationSession 生成普通运行请求
  -> Provider.stream(已提交历史 + 本轮临时上下文, 全量工具)
  -> 文本增量事件实时转发给 TerminalUI
  -> StreamCompleted 返回完整助手消息 + TokenUsage
  -> 如果无工具调用：提交本轮历史，结束
  -> 如果有工具调用：按安全类别分批执行工具
  -> 工具结果按原顺序写入临时上下文
  -> 进入下一次模型迭代
```

### 多工具批次

```text
[read_file, search_code, write_file, find_files, read_file, run_command]
  -> 批次 1：read_file + search_code 并发
  -> 批次 2：write_file 串行
  -> 批次 3：find_files + read_file 并发
  -> 批次 4：run_command 串行
  -> 结果按原始顺序回写
```

### Plan Mode

```text
/plan 修复某问题
  -> 只暴露 read_file / find_files / search_code
  -> Agent Loop 可多轮调研
  -> 最终文本成功产生
  -> 保存为当前会话最近计划

/do
  -> 读取最近计划
  -> 生成执行请求
  -> 暴露全量工具
  -> 按普通 Agent Loop 执行
```

## 文件组织

```text
src/okcode/
├── app.py                 # REPL 主循环，消费会话事件
├── conversation.py        # 会话历史、Plan Mode、Agent Loop 编排
├── models.py              # ChatMessage、ToolCall、TokenUsage、TurnEvent 等领域模型
├── terminal.py            # Rich 终端事件渲染
├── providers/
│   ├── base.py            # Provider 协议接口
│   ├── openai.py          # OpenAI 多工具调用、用量与历史序列化
│   └── anthropic.py       # Anthropic 多 tool_use、用量与历史序列化
└── tools/
    ├── models.py          # ToolDefinition、ToolSafety、ToolExecutionResult
    ├── registry.py        # 工具注册与安全类别过滤
    ├── executor.py        # 单工具执行、超时和错误封装
    ├── files.py           # 文件读写改工具安全类别声明
    ├── search.py          # 查找/搜索工具安全类别声明
    └── command.py         # 命令工具安全类别声明

tests/
├── fakes.py                       # 可控 Provider 和终端替身扩展
├── unit/test_conversation.py      # Agent Loop、Plan Mode、停止条件
├── unit/test_tools_registry.py    # 工具安全类别过滤
├── integration/test_openai_sse.py # OpenAI 多工具调用与历史回写
├── integration/test_anthropic_sse.py # Anthropic 多工具调用与历史回写
└── integration/test_tool_turn.py  # 端到端多轮工具链路
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Agent Loop 位置 | 放在会话层编排 | 会话层已经拥有历史、Provider、工具注册表和执行器，便于实现原子提交 |
| 多工具消息结构 | 助手消息持有工具调用元组，工具消息持有结果元组 | 同一轮多工具调用需要一次性保留调用和结果对应关系 |
| 工具并发依据 | 工具定义显式声明安全类别 | 避免靠名称硬编码判断，后续新增工具也能安全扩展 |
| Provider 多工具策略 | Provider 返回全部工具调用，不再只取第一个 | 满足本阶段多工具调用和批次调度需求 |
| Token 用量位置 | Provider 收集，Agent Loop 统一转成事件 | 用量来自协议层，但展示和统计属于运行事件 |
| `/plan` 工具范围 | 只读工具过滤 | 符合“不做权限系统”的边界，又能保证计划阶段不修改工作区 |
| `/do` 输入来源 | 当前会话最近一次成功计划 | 符合已确认交互：用户输入 `/do` 默认执行最近计划 |
| 停止后历史提交 | 仅最终文本成功时提交 | 保持现有原子提交语义，避免失败任务污染上下文 |
| 迭代上限 | 默认 12 | 作为循环兜底安全网，防止模型无限请求工具 |
| 未知工具上限 | 连续 2 次 | 给模型一次纠错机会，同时避免反复卡在错误工具名 |

## Spec 覆盖

| Spec 项 | 设计归属 |
|---|---|
| F1 ReAct 循环 | `conversation.py` Agent Loop |
| F2 流式双路收集 | Provider 完整完成事件 + 会话层实时转发增量 |
| F3 异步事件流 | `models.py` 事件模型 + `terminal.py` 消费 |
| F4 多工具分批 | `ToolSafety` + 会话层批次调度 |
| F5 Plan Mode | `conversation.py` 命令识别和最近计划状态 |
| F6 停止条件 | `AgentConfig` + 会话层停止判断 |
| F7 历史提交语义 | `ConversationSession` 临时上下文和成功提交 |
| N1 协议兼容 | OpenAI / Anthropic Provider 改造 |
| N2 运行稳定 | 停止事件、异常边界、取消不提交 |
| N3 顺序稳定 | 批次执行结果按原始索引排序回写 |
| N4 界面解耦 | 终端只渲染事件 |
| N5 可测试性 | 可控 Provider、假工具、无真实副作用测试 |
| N6 向后兼容 | 保留现有文本/单工具行为作为测试场景 |
