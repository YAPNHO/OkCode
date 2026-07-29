# OkCode 第四阶段：系统提示与缓存策略 Plan

## 架构概览

本阶段在现有 ConversationSession -> LLMProvider -> ToolRegistry 链路中插入一个提示构建层。提示构建层不执行工具、不提交历史，只负责把稳定系统指令、动态环境信息、任务模式补充指令和工具描述整理成 Provider 可序列化的请求上下文。

新的数据流如下：

1. 终端把用户输入交给 ConversationSession。
2. ConversationSession 识别普通任务、/plan 或 /do，并为当前模型迭代生成 PromptBuildContext。
3. PromptBuilder 输出 PromptBundle，其中稳定系统块与动态补充块分开保存。
4. ToolDefinitionEnhancer 在模型可见层增强工具描述，保持工具执行器使用的注册表语义不变。
5. ConversationSession 用 ProviderRequest 调用 Provider；对话历史仍只提交用户、助手、工具结果消息，系统提示和补充指令不写入普通历史。
6. Provider 根据协议把 PromptBundle 序列化为 OpenAI Chat Completions 或 Anthropic Messages 请求，并解析缓存命中用量。
7. 终端沿用现有事件流展示文本、工具状态和 Token 用量；缓存字段只作为用量事件的补充信息出现。

这样可以让 Agent Loop 继续保持“运行循环与历史提交”的职责，Provider 继续只做协议适配，提示策略集中在独立模块内。

## 核心数据结构

### PromptSection

表示一段可拼装的提示模块。

- name: str：模块名，例如“身份”“系统约束”。
- priority: int：排序用优先级，数值越小越靠前。
- content: str：模块正文。
- cacheable: bool：该模块是否属于稳定缓存前缀。

固定七模块全部为 cacheable=True。环境信息和运行时补充指令为 cacheable=False。自定义指令、Skill、长期记忆本阶段先作为可选模块接口存在，默认由调用方决定是否 cacheable；由于实际加载留给后续章节，本阶段测试只覆盖空内容和显式传入内容。

### PromptBundle

表示一轮模型请求的完整提示上下文。

- stable_system: str：固定七模块拼成的稳定系统指令。
- dynamic_system: tuple[SystemInstruction, ...]：环境信息、任务模式提醒和运行时补充指令。
- debug_full_prompt: str：用于测试和调试的完整提示文本，顺序为固定模块、环境信息、可选模块。
- cache_key: str：由稳定系统文本和增强后工具描述的摘要生成，用于支持 Provider 的缓存路由。

debug_full_prompt 只用于测试、人工排查或显式调试，不默认打印到终端。

### SystemInstruction

表示一条不会写入用户历史的系统级补充消息。

- tag: str：特殊标签名，统一使用 okcode-system-note。
- kind: Literal["environment", "runtime", "task_mode", "custom", "skill", "memory"]：补充指令来源。
- content: str：指令正文。
- priority: int：同一请求中多个补充指令的排序。

序列化文本统一形如：

~~~text
<okcode-system-note kind="environment">
...
</okcode-system-note>
~~~

该标签只表达“这是系统级补充上下文，不是用户要你回复的问题”，不作为 XML 解析协议依赖。

### PromptBuildContext

表示提示构建时可用的运行时事实。

- workspace_root: str：当前工作区根目录。
- platform: str：操作系统和 shell 摘要。
- current_date: str：当前日期。
- available_tool_names: tuple[str, ...]：当前暴露给模型的工具名。
- turn_kind: TurnKind：普通任务、规划任务或执行已保存计划。
- iteration: int：本轮 Agent Loop 的模型迭代次数。
- task_mode: TaskMode | None：当前会话级任务模式。
- optional_sections: PromptOptionalSections：自定义指令、Skill、长期记忆等后续扩展入口。

### TaskModeSchedule

控制任务模式指令按轮次注入。

- full_on_first: bool = True：第 1 次模型请求注入完整规则。
- repeat_every: int = 4：之后每隔 4 次模型请求注入关键规则。
- compact_otherwise: bool = True：其他轮次只注入精简提醒。

/plan 使用规划模式规则，/do 使用执行计划规则，普通任务没有额外任务模式补充。该计数以一次 stream_turn 内的模型迭代为单位，不跨用户轮次持久化。

### ProviderRequest

替代当前 Provider 的 messages + tools 双参数输入。

- messages: tuple[ChatMessage, ...]：已提交历史加本轮临时消息。
- tools: tuple[ToolDefinition, ...]：增强后的模型可见工具定义。
- prompt: PromptBundle：系统提示与补充指令。
- cache: PromptCachePolicy：缓存开关、显式或隐式模式、TTL 和兼容策略。

这样 Provider 可以统一拿到完整请求语义，而不用从普通消息中猜系统提示。

### PromptCacheUsage

归一化缓存用量字段。

- read_tokens: int | None：从缓存读取的输入 token。
- write_tokens: int | None：本次写入缓存的输入 token。
- available: bool：Provider 是否返回了缓存字段。

TokenUsage 增加 cache: PromptCacheUsage 字段。OpenAI 兼容协议从 prompt_tokens_details.cached_tokens 读取缓存命中数量，write_tokens 保持 None，除非具体兼容服务明确返回写入字段；Anthropic 协议从 usage.cache_read_input_tokens 和 usage.cache_creation_input_tokens 读取。字段缺失时 available=False，不估算。

## 核心接口

### PromptBuilder.build(context, tools)

输入运行时上下文和当前可见工具，输出 PromptBundle。

职责：

- 生成七个固定稳定模块。
- 生成环境信息补充指令。
- 按固定顺序追加可选模块。
- 计算稳定缓存摘要。
- 生成 debug_full_prompt 供单元测试断言模块顺序。

### TaskModeInstructionPlanner.build(turn_kind, iteration)

输入任务类型和模型迭代次数，输出零个或一个 SystemInstruction。

规则：

- turn_kind=PLAN 且 iteration=1：输出完整规划规则。
- turn_kind=PLAN 且 iteration % repeat_every == 0：输出关键规则。
- turn_kind=PLAN 其他轮次：输出精简提醒。
- turn_kind=DO：输出“执行已保存计划”的精简约束，不重复完整规划规则。
- 普通任务：不输出任务模式补充指令。

### enhance_tool_definitions(tools)

输入注册表返回的工具定义，输出模型可见工具定义。

增强方式：

- 保持 name、input_schema、timeout_seconds 和 safety 不变。
- 在 description 后追加短规则，不改变执行器和 schema 校验。
- read_file 强化“编辑或回答文件细节前优先读取目标文件”。
- find_files 和 search_code 强化“查找路径和代码引用时优先用搜索工具，不编造文件位置”。
- write_file 和 edit_file 强化“写入或编辑前必须先读或确认目标内容，避免覆盖未知用户改动”。
- run_command 强化“用于验证、测试和必要诊断；有副作用命令要说明目的并避免破坏性操作”。

### LLMProvider.stream(request)

Provider 协议改为接收 ProviderRequest。

OpenAI 适配：

- 将 prompt.stable_system 序列化为最前面的 system 消息。
- 默认依赖稳定前缀顺序和 prompt_cache_key 提高缓存命中率，不发送未确认支持的显式缓存断点字段。
- 将 prompt.dynamic_system 序列化为紧随其后的 developer 消息；若兼容服务不支持 developer，降级为第二条 system 消息。
- 将普通 ChatMessage 历史按现有规则序列化。
- 保持 stream_options={"include_usage": True}，并解析缓存字段。
- 对 OpenAI 兼容服务默认不强行发送 prompt_cache_key、prompt_cache_retention 或显式缓存参数；只有配置确认支持时才发送，避免破坏 DeepSeek 等兼容服务。

Anthropic 适配：

- 将稳定系统提示序列化为 system 数组中的第一个 text block，并在该 block 上放 cache_control。
- 将动态补充指令序列化为后续 system text blocks，不放 cache_control。
- 工具定义保持稳定排序；如启用工具缓存策略，可在最后一个稳定工具定义上放 cache_control，但本阶段优先用稳定 system block 的断点覆盖工具和系统前缀。
- 普通 ChatMessage 历史按现有 Messages 规则序列化，工具结果仍映射为 user content 中的 tool_result block。
- 解析 cache_read_input_tokens 和 cache_creation_input_tokens。

### ConversationSession._build_provider_request(...)

在每次模型迭代前构造 ProviderRequest。

职责：

- 保持现有 pending 临时历史和成功后原子提交逻辑。
- 根据当前命令类型生成 TurnKind。
- 只把普通用户消息、助手消息和工具结果放入 pending。
- 每次迭代重新生成动态环境信息和任务模式补充指令。
- 将增强后的工具定义传给 Provider。

## 模块设计

### src/okcode/prompt/builder.py

职责：提示拼装主入口。

对外接口：

- PromptBuilder.build(context: PromptBuildContext, tools: Sequence[ToolDefinition]) -> PromptBundle
- render_debug_prompt(bundle: PromptBundle) -> str

内部逻辑只做字符串拼装、排序和摘要，不访问文件系统、不调用模型。

### src/okcode/prompt/sections.py

职责：七个固定模块和环境信息模块的文本来源。

固定模块：

1. 身份：说明 OkCode 是本地终端 AI 编程助手，面向代码阅读、编辑、测试和解释。
2. 系统约束：说明遵守用户目标、保持安全边界、不泄露密钥、不伪造工具结果。
3. 任务模式：说明普通任务、规划任务和执行计划的行为差异。
4. 动作执行：说明回答、诊断、规划不默认改代码；修复、实现请求要执行并验证。
5. 工具使用：说明优先专用工具、编辑前先读、读写顺序和工具结果真实性。
6. 语气风格：说明简体中文、直接、适合学习者、解释关键 Python 和 Agent 概念。
7. 文本输出：说明结论先行、引用文件路径、避免冗长、必要时给验证命令。

环境信息模块由 PromptBuildContext 渲染，单独标记为动态补充指令。

### src/okcode/prompt/modes.py

职责：任务模式补充指令的轮次策略。

包含：

- TurnKind
- TaskMode
- TaskModeSchedule
- TaskModeInstructionPlanner

ConversationSession.stream_turn 识别 /plan 和 /do 后传入 TurnKind，不在用户消息正文里硬塞模式说明。

### src/okcode/prompt/cache.py

职责：缓存策略和摘要。

包含：

- PromptCachePolicy
- PromptCacheUsage
- build_cache_key(stable_system, tools)

build_cache_key 使用稳定系统文本、增强后工具定义的名称、描述和 schema 摘要，动态环境、用户消息和历史不参与。

### src/okcode/prompt/tools.py

职责：工具描述增强。

只返回新的 ToolDefinition，不修改注册表对象本身，避免影响执行器和已有测试替身。

### src/okcode/models.py

职责：扩展通用领域模型。

改动：

- 增加 PromptCacheUsage 或从 okcode.prompt.cache 引用该结构。
- TokenUsage 增加 cache 字段。
- 增加 ProviderRequest。
- 保持 ChatMessage 的三种业务角色不变，不新增持久化的 SYSTEM 历史角色。

不把系统提示放入 ChatMessage，避免系统指令误进入会话历史。

### src/okcode/providers/openai.py

职责：OpenAI Chat Completions 请求序列化和缓存字段解析。

改动：

- stream 接收 ProviderRequest。
- _serialize_prompt_messages(prompt) 生成系统和开发者补充消息。
- _serialize_messages 继续处理用户、助手、工具消息。
- _usage_from_object 读取普通 Token 和缓存 Token。
- 对不支持显式缓存的兼容服务保持旧请求形态。

### src/okcode/providers/anthropic.py

职责：Anthropic Messages 请求序列化和缓存字段解析。

改动：

- stream 接收 ProviderRequest。
- _serialize_system(prompt) 生成 system text block 数组。
- _serialize_tool_definition 可按缓存策略附加 cache_control。
- _usage_from_event 读取普通 Token 和缓存 Token。

### src/okcode/conversation.py

职责：把 Agent Loop 的每次模型迭代改为构造 ProviderRequest。

改动：

- ConversationSession.__init__ 增加可选 prompt_builder 和 runtime_context_factory。
- _run_agent 在每次 Provider 调用前生成 ProviderRequest。
- /plan、/do 不再只靠用户消息正文表达模式约束，而是通过 TurnKind 驱动任务模式补充指令。
- 历史提交规则保持不变：只有成功得到最终文本才提交本轮普通消息。

## 模块交互

普通任务：

~~~text
用户输入
  -> ConversationSession 识别 TurnKind.NORMAL
  -> registry.definitions()
  -> enhance_tool_definitions()
  -> PromptBuilder.build()
  -> ProviderRequest
  -> Provider.stream()
  -> Agent Loop 处理文本、工具调用、用量
~~~

规划任务：

~~~text
/plan 任务
  -> TurnKind.PLAN
  -> 只暴露 READ_ONLY 工具
  -> 第 1 次迭代注入完整规划规则
  -> 若模型继续调用工具，第 4/8/12 次迭代注入关键规则，其余精简提醒
  -> 成功最终文本保存为 SavedPlan
~~~

执行已保存计划：

~~~text
/do
  -> TurnKind.DO
  -> 暴露全量工具
  -> 用户消息只包含“执行最近计划 + 计划正文”
  -> 系统补充指令说明按计划执行、先读后改、验证后报告
~~~

Provider 序列化顺序：

~~~text
稳定系统提示（可缓存）
动态系统补充（不可缓存）
已提交历史
本轮临时消息
工具定义（稳定排序、增强描述）
~~~

具体协议可能把工具定义放在请求顶层，但内部语义始终保持“稳定工具定义 + 稳定系统提示优先，动态内容靠后”。

## 文件组织

~~~text
src/okcode/
  prompt/
    __init__.py          # 导出提示系统公共类型
    builder.py           # PromptBuilder、PromptBundle
    sections.py          # 固定模块和环境信息渲染
    modes.py             # TurnKind、TaskModeSchedule、任务模式注入
    cache.py             # PromptCachePolicy、PromptCacheUsage、cache_key
    tools.py             # 工具描述增强
  models.py              # ProviderRequest、TokenUsage 扩展
  conversation.py        # 接入 PromptBuilder 和 ProviderRequest
  providers/
    openai.py            # OpenAI 系统消息、缓存字段解析
    anthropic.py         # Anthropic system blocks、cache_control、缓存字段解析
  tools/
    models.py            # ToolDefinition 保持不变

tests/
  unit/
    test_prompt_builder.py
    test_prompt_modes.py
    test_prompt_tools.py
    test_models.py
    test_conversation.py
  integration/
    test_openai_sse.py
    test_anthropic_sse.py
  manual/
    phase4_prompt_scenarios.md
~~~

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 系统提示是否进入 ChatMessage | 不进入 | ChatMessage 当前表示可提交历史；系统提示是请求级上下文，混入历史会污染会话和缓存边界 |
| Provider 输入形态 | 用 ProviderRequest 替代 messages + tools | 统一承载提示、历史、工具和缓存策略，避免参数继续横向膨胀 |
| OpenAI 缓存默认值 | 默认稳定前缀 + 用量观测；确认支持后启用 prompt_cache_key 或保留策略 | 当前还支持 DeepSeek 等 OpenAI 兼容服务，默认发送新字段可能导致请求被拒 |
| Anthropic 缓存策略 | 稳定 system block 显式 cache_control | 能明确把动态环境和用户消息排除在缓存断点之后，符合本阶段目标 |
| 任务模式计数粒度 | 单个 stream_turn 内的模型迭代次数 | 现有 Agent Loop 已以迭代为单位调用 Provider，最小改动即可实现“首轮完整、间隔重复、其余精简” |
| 工具描述增强位置 | 请求构建时包装定义 | 不改变工具注册表和执行器，降低回归风险 |
| 缓存字段缺失处理 | PromptCacheUsage.available=False | 避免把没有返回的缓存信息误当成 0 命中 |
| 人工评估产物 | Markdown 场景文档 | 本阶段不做自动化评估，Markdown 足以描述输入、观察点和预期差异 |

## 终端缓存可见性

终端继续只消费 TurnEvent。当 TokenUsageReported 中的缓存用量可用时，终端在现有 Token 摘要中追加“缓存读取”和“缓存写入”；缓存字段缺失时不显示伪造的 0 命中。该行为由终端单元测试覆盖。

## Spec 覆盖关系

- F1、F2：PromptBuilder、PromptSection、sections.py 覆盖固定模块和环境信息顺序。
- F3：PromptOptionalSections 和 debug_full_prompt 覆盖可选模块追加。
- F4：PromptBundle.stable_system、dynamic_system、ProviderRequest 和 PromptCachePolicy 覆盖稳定/动态分层。
- F5：固定“工具使用”模块和 enhance_tool_definitions 覆盖双重强化。
- F6：SystemInstruction 和 Provider 系统级序列化覆盖特殊标签补充消息。
- F7：TaskModeInstructionPlanner 覆盖任务模式按轮次注入。
- F8：PromptCacheUsage 和两个 Provider 的 usage 解析覆盖缓存观测。
- F9：tests/manual/phase4_prompt_scenarios.md 覆盖人工对比场景。
- N4、N5：现有 Agent Loop 测试加新增单元和集成测试覆盖兼容性和可测试性。

## 测试策略

### 单元测试

- test_prompt_builder.py
  - 断言固定模块顺序和空行分隔。
  - 断言环境信息只出现在动态补充和 debug 完整提示尾部。
  - 断言可选模块为空时不输出标题。
  - 断言只改变日期或用户输入时 stable_system 和 cache_key 不变。

- test_prompt_modes.py
  - 断言 /plan 第 1 次迭代输出完整规则。
  - 断言第 4 次迭代输出关键规则。
  - 断言第 2、3、5 次输出精简提醒。
  - 断言普通任务不输出任务模式补充。

- test_prompt_tools.py
  - 断言工具名、schema、timeout、safety 不变。
  - 断言工具描述包含对应关键规则。
  - 断言增强结果排序稳定。

- test_models.py
  - 断言 PromptCacheUsage.unavailable() 行为。
  - 断言 TokenUsage 兼容旧的 input/output/total 字段。

### Provider 集成替身测试

- test_openai_sse.py
  - 断言请求最前面包含稳定 system 消息。
  - 断言动态补充消息带 okcode-system-note 标签，且不在 user 历史中。
  - 断言配置确认支持缓存路由时发送 prompt_cache_key，默认兼容模式不发送新字段。
  - 断言 prompt_tokens_details.cached_tokens 被归一化为 cache.read_tokens，write_tokens 保持 None。

- test_anthropic_sse.py
  - 断言 system 是稳定 block 加动态 block 的数组。
  - 断言稳定 block 带 cache_control，动态 block 不带。
  - 断言工具结果仍按现有 user tool_result 结构回传。
  - 断言 cache_read_input_tokens/cache_creation_input_tokens 被归一化。

### Agent Loop 回归测试

- 扩展 FakeProvider 记录 ProviderRequest。
- 保持成功提交、失败回滚、多工具调度、/plan、/do 既有断言。
- 新增断言系统提示和任务模式补充不进入 session.messages。

### 人工场景

tests/manual/phase4_prompt_scenarios.md 包含六类场景：

1. 让模型解释一个具体文件，观察是否先调用 read_file。
2. 让模型定位函数或类，观察是否优先 search_code。
3. 让模型修改文件，观察是否先读目标内容再编辑。
4. 使用 /plan 做调研，观察只读工具和规划规则是否稳定。
5. 连续两轮改变日期或环境信息，观察缓存稳定字段不变。
6. 用支持缓存字段的可控响应，观察用量事件是否显示 cache read/write。

## 实施边界

- 不在本阶段加载真实项目指令文件。
- 不实现自动记忆或 Skill 自动发现，只保留可选模块入口。
- 不接入真实 MCP。
- 不做自动评分器。
- 不改变工具执行器权限和并发调度。
- 不默认打印完整系统提示；如需调试，后续可加显式调试命令。
