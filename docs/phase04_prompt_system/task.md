# OkCode 第四阶段：系统提示与缓存策略 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | src/okcode/prompt/__init__.py | 导出提示系统的公共类型和构建入口 |
| 新建 | src/okcode/prompt/cache.py | 缓存策略、缓存用量和稳定 cache key |
| 新建 | src/okcode/prompt/sections.py | 七个固定模块、环境信息和可选模块文本 |
| 新建 | src/okcode/prompt/builder.py | PromptSection、SystemInstruction、PromptBundle、PromptBuilder |
| 新建 | src/okcode/prompt/modes.py | TurnKind、任务模式与按轮次注入策略 |
| 新建 | src/okcode/prompt/tools.py | 模型可见工具描述增强 |
| 修改 | src/okcode/models.py | ProviderRequest、TokenUsage 缓存字段、ProviderConfig 缓存开关 |
| 修改 | src/okcode/config.py | 解析和校验 Provider 的 prompt_cache 配置 |
| 修改 | config.yaml | 补充示例缓存配置及兼容说明 |
| 修改 | src/okcode/providers/base.py | Provider 协议改为接收 ProviderRequest |
| 修改 | src/okcode/conversation.py | 每次 Agent Loop 迭代构造 ProviderRequest |
| 修改 | src/okcode/cli.py | 将 Workspace 运行时信息传给 ConversationSession |
| 修改 | src/okcode/providers/openai.py | 系统/动态消息序列化、缓存路由、缓存用量解析 |
| 修改 | src/okcode/providers/anthropic.py | system blocks、cache_control、缓存用量解析 |
| 修改 | tests/fakes.py | 记录 ProviderRequest 的测试替身 |
| 新建 | tests/unit/test_prompt_builder.py | 模块排序、动态分层、可选模块和 cache key 测试 |
| 新建 | tests/unit/test_prompt_modes.py | 规划模式首轮、间隔、精简提醒测试 |
| 新建 | tests/unit/test_prompt_tools.py | 工具描述增强和元数据保持测试 |
| 修改 | tests/unit/test_models.py | 缓存用量和 ProviderRequest 测试 |
| 修改 | tests/unit/test_config.py | prompt_cache 配置校验测试 |
| 修改 | tests/unit/test_conversation.py | 请求级提示、历史隔离和 Plan Mode 集成测试 |
| 修改 | tests/integration/test_openai_sse.py | OpenAI 请求体和缓存 usage 解析测试 |
| 修改 | tests/integration/test_anthropic_sse.py | Anthropic system blocks、cache_control、usage 测试 |
| 新建 | tests/manual/phase4_prompt_scenarios.md | 六类人工对比场景和记录模板 |

## T1：建立缓存策略与缓存用量类型

**文件：** src/okcode/prompt/cache.py、src/okcode/prompt/__init__.py  
**依赖：** 无

**步骤：**

1. 定义 PromptCachePolicy，包含启用状态、协议兼容模式、显式路由开关和 TTL。
2. 定义 PromptCacheUsage，包含 read_tokens、write_tokens、available 以及 unavailable 工厂方法。
3. 实现 build_cache_key，输入稳定系统文本和已增强工具定义，输出稳定、不可逆且不含动态环境信息的摘要。
4. 在提示包导出模块中只暴露本阶段需要的缓存公共类型。

**验证：**

~~~text
uv run python -c "from okcode.prompt.cache import PromptCacheUsage; assert PromptCacheUsage.unavailable().available is False"
~~~

预期：缓存字段缺失时不伪造 0；相同稳定输入生成相同 key。

## T2：定义七个固定提示模块和可选模块

**文件：** src/okcode/prompt/sections.py  
**依赖：** T1

**步骤：**

1. 以函数或常量定义身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出七个稳定模块。
2. 固定模块按优先级返回，不在模块内部拼接环境信息。
3. 定义环境信息渲染函数，输入工作区、平台、日期和可用工具，输出动态文本。
4. 定义自定义指令、已激活 Skill、长期记忆三个可选模块的标题和固定排序。
5. 在“工具使用”固定模块中写入专用工具优先、编辑前先读、禁止伪造工具结果和副作用谨慎四条规则。

**验证：**

~~~text
uv run python -c "from okcode.prompt.sections import fixed_sections; assert len(fixed_sections()) == 7"
~~~

预期：固定模块顺序稳定；空可选模块不产生标题。

## T3：实现提示包与稳定/动态拼装

**文件：** src/okcode/prompt/builder.py、src/okcode/prompt/__init__.py  
**依赖：** T1、T2

**步骤：**

1. 定义 PromptSection、SystemInstruction、PromptBuildContext、PromptOptionalSections 和 PromptBundle。
2. 为 SystemInstruction 实现带 okcode-system-note 标签的文本渲染。
3. 实现 PromptBuilder.build：按优先级拼接稳定模块，单独生成环境补充消息，并按环境信息之后的顺序生成可选模块。
4. 生成仅供测试和显式调试的 debug_full_prompt，不把它接入终端输出。
5. 用稳定系统文本和增强工具定义调用 build_cache_key；日期、工作区状态、用户输入和动态补充不参与 key。

**验证：**

~~~text
uv run python -c "from okcode.prompt.builder import PromptBuilder; assert PromptBuilder"
~~~

预期：完整提示文本满足七模块加环境信息顺序；动态变化不改变 stable_system 和 cache_key。

## T4：实现任务模式按轮次注入

**文件：** src/okcode/prompt/modes.py、src/okcode/prompt/__init__.py  
**依赖：** T3

**步骤：**

1. 定义 TurnKind，至少包含 NORMAL、PLAN、DO。
2. 定义 TaskModeSchedule，默认首轮完整、每 4 次迭代重复关键规则、其余轮次精简。
3. 实现 TaskModeInstructionPlanner，根据 TurnKind 和 iteration 生成零个或一个 SystemInstruction。
4. 为 PLAN 写入只读调研、先理解后规划、禁止修改文件的规则。
5. 为 DO 写入执行最近计划、先读后改、验证后报告的精简规则。

**验证：**

~~~text
uv run python -c "from okcode.prompt.modes import TaskModeInstructionPlanner; assert TaskModeInstructionPlanner"
~~~

预期：PLAN 的第 1、4、8 次规则分别符合完整、关键、关键策略；NORMAL 不生成模式补充。

## T5：实现模型可见工具描述增强

**文件：** src/okcode/prompt/tools.py、src/okcode/prompt/__init__.py  
**依赖：** T2

**步骤：**

1. 实现 enhance_tool_definitions，返回新的 ToolDefinition 元组，不修改 ToolRegistry 内原对象。
2. 为 read_file、find_files、search_code 追加专用读取和检索规则。
3. 为 write_file、edit_file 追加编辑前读取或确认目标内容的规则。
4. 为 run_command 追加验证目的和谨慎执行副作用命令的规则。
5. 保持工具名称、schema、timeout、safety 和原有排序不变。

**验证：**

~~~text
uv run python -c "from okcode.prompt.tools import enhance_tool_definitions; assert enhance_tool_definitions"
~~~

预期：每个工具的元数据保持不变，模型描述同时包含原能力和补充规则。

## T6：扩展领域模型和 Provider 配置

**文件：** src/okcode/models.py、src/okcode/config.py、config.yaml、tests/unit/test_models.py、tests/unit/test_config.py  
**依赖：** T1、T3、T4

**步骤：**

1. 定义 ProviderRequest，携带普通消息、增强工具、PromptBundle 和 PromptCachePolicy。
2. 为 TokenUsage 增加 cache 字段，默认使用 PromptCacheUsage.unavailable，保持既有构造调用可用。
3. 为 ProviderConfig 增加可选 prompt_cache 开关，默认关闭。
4. 扩展 YAML 允许字段和校验：prompt_cache 必须为布尔值，省略时使用 false。
5. 在 config.yaml 示例中说明 OpenAI 兼容 Provider 默认只观测缓存，需确认服务支持后再开启缓存路由；Anthropic 可启用显式缓存。

**验证：**

~~~text
uv run pytest tests/unit/test_models.py tests/unit/test_config.py -q
~~~

预期：旧配置仍可加载；非法 prompt_cache 值产生明确配置错误。

## T7：改造 Provider 抽象与测试替身

**文件：** src/okcode/providers/base.py、tests/fakes.py  
**依赖：** T6

**步骤：**

1. 将 LLMProvider.stream 的参数替换为单个 ProviderRequest。
2. 更新 FakeProvider，使其记录每次请求对象、请求中的普通消息和工具定义。
3. 保留 FakeProvider 的流事件脚本、关闭状态和旧测试断言能力。
4. 确保 Protocol 类型检查和 FakeProvider 调用方都使用新签名。

**验证：**

~~~text
uv run python -c "from tests.fakes import FakeProvider; assert FakeProvider"
~~~

预期：Provider 抽象和测试替身都接受 ProviderRequest；完整会话回归由 T8 和 T15 执行。

## T8：在 ConversationSession 构造请求级提示

**文件：** src/okcode/conversation.py、src/okcode/cli.py、tests/unit/test_conversation.py  
**依赖：** T3、T4、T5、T6、T7

**步骤：**

1. 给 ConversationSession 注入 PromptBuilder、Workspace 运行时信息工厂和缓存策略；未传入时提供可测默认实现。
2. 将 stream_turn 的普通、PLAN、DO 分支转换为携带 TurnKind 的内部调用。
3. 在每次 Agent Loop 迭代前，基于当前迭代次数、当前可见工具和环境信息构造 ProviderRequest。
4. 把工具定义先送入 enhance_tool_definitions，再放入 ProviderRequest；执行器仍使用原 ToolRegistry。
5. 保持 pending 只含用户、助手和工具结果，确认系统提示、环境信息和模式补充永不写入 session.messages。
6. 在 CLI 中把 Workspace 根目录和运行平台信息传入会话，避免 PromptBuilder 在业务逻辑里直接读取当前目录。

**验证：**

~~~text
uv run pytest tests/unit/test_conversation.py -q
~~~

预期：普通会话、失败回滚、多工具批次、/plan、/do 仍通过；请求对象含提示包但提交历史不含系统指令。

## T9：实现 OpenAI 提示消息与兼容缓存路由序列化

**文件：** src/okcode/providers/openai.py  
**依赖：** T6、T7、T8

**步骤：**

1. 改造 stream 读取 ProviderRequest，并从 request.prompt 生成最前面的稳定 system 消息。
2. 把动态 SystemInstruction 序列化为 developer 消息；为兼容服务提供 system 消息降级路径。
3. 将普通历史继续按当前 user、assistant、tool 规则序列化。
4. 在 prompt_cache=false 时保持现有 OpenAI 兼容请求形态，不发送新缓存参数。
5. 在 prompt_cache=true 时发送稳定 prompt_cache_key；仅在明确支持时发送可选保留策略字段。
6. 保持 thinking 扩展、工具定义、stream_options 和流式工具调用拼装行为不变。

**验证：**

~~~text
uv run pytest tests/integration/test_openai_sse.py -q -k "request_body or serializes_history"
~~~

预期：system 和动态补充在普通历史之前；关闭缓存时请求体不出现缓存路由字段。

## T10：实现 OpenAI 缓存用量解析

**文件：** src/okcode/providers/openai.py、tests/integration/test_openai_sse.py  
**依赖：** T6、T9

**步骤：**

1. 从流末 usage 的 prompt_tokens_details.cached_tokens 读取缓存命中。
2. OpenAI 未返回缓存写入字段时令 write_tokens 为 None，不把缺失解释为 0。
3. 将普通 input、output、total token 与 PromptCacheUsage 一并放入 TokenUsage。
4. 保留 usage 缺失时的 TokenUsage.unavailable 行为。
5. 增加包含和缺失 cached_tokens 的 SSE 替身响应。

**验证：**

~~~text
uv run pytest tests/integration/test_openai_sse.py -q -k "usage or cache"
~~~

预期：cached_tokens 正确映射为 read_tokens；字段缺失时 cache.available 为 false。

## T11：实现 Anthropic system blocks 与显式缓存

**文件：** src/okcode/providers/anthropic.py  
**依赖：** T6、T7、T8

**步骤：**

1. 改造 stream 读取 ProviderRequest。
2. 将 stable_system 序列化为 system 数组第一个 text block，并在 prompt_cache 启用时加 cache_control。
3. 将动态 SystemInstruction 作为后续 system text blocks，不附加 cache_control。
4. 让工具定义维持稳定排序；当缓存启用且已有工具时，按策略只在最后一个稳定工具定义添加 cache_control。
5. 保持 thinking、tool_choice、工具结果回传和现有流式事件处理不变。

**验证：**

~~~text
uv run pytest tests/integration/test_anthropic_sse.py -q -k "request_body or serializes_history or thinking"
~~~

预期：system 块顺序为稳定块在前、动态块在后；动态块不会带 cache_control。

## T12：实现 Anthropic 缓存用量解析

**文件：** src/okcode/providers/anthropic.py、tests/integration/test_anthropic_sse.py  
**依赖：** T6、T11

**步骤：**

1. 从每个可用 usage 对象读取 cache_read_input_tokens 和 cache_creation_input_tokens。
2. 将读取值映射到 PromptCacheUsage.read_tokens 和 write_tokens。
3. 流中间与 final message 都有 usage 时，保留最后一个完整值。
4. 两个缓存字段都缺失时保持 cache.available=false。
5. 增加带缓存字段和不带缓存字段的 SSE 替身响应。

**验证：**

~~~text
uv run pytest tests/integration/test_anthropic_sse.py -q -k "usage or cache"
~~~

预期：读取和创建 token 分别正确映射；字段缺失时不填估算值。

## T13：补齐提示构建和缓存稳定性单元测试

**文件：** tests/unit/test_prompt_builder.py  
**依赖：** T3、T5、T6

**步骤：**

1. 构造包含全部固定模块、环境信息和三个可选模块的上下文。
2. 断言 debug_full_prompt 的七模块、环境信息和可选模块顺序，以及相邻模块之间的空行。
3. 分别省略三个可选模块，断言没有空标题或多余占位符。
4. 只改变日期、工作区状态或用户消息，断言 stable_system 和 cache_key 字节级一致。
5. 改变固定模块或工具描述，断言 cache_key 变化。
6. 断言动态环境只以带 okcode-system-note 标签的 SystemInstruction 出现。

**验证：**

~~~text
uv run pytest tests/unit/test_prompt_builder.py -q
~~~

预期：F1、F2、F3、F4、F6 的核心行为均有离线断言。

## T14：补齐模式策略和工具规则单元测试

**文件：** tests/unit/test_prompt_modes.py、tests/unit/test_prompt_tools.py  
**依赖：** T4、T5

**步骤：**

1. 覆盖 PLAN 首轮完整、间隔关键、其余精简和 NORMAL 无补充四种模式策略。
2. 覆盖 DO 的执行计划精简规则。
3. 对六个默认工具分别断言其描述中含有对应关键约束。
4. 对任意工具定义断言增强前后 name、input_schema、timeout_seconds、safety 保持一致。
5. 断言增强结果的工具顺序不依赖字典插入顺序。

**验证：**

~~~text
uv run pytest tests/unit/test_prompt_modes.py tests/unit/test_prompt_tools.py -q
~~~

预期：F5、F7 的策略在不访问真实模型服务时完全可验证。

## T15：补齐会话历史隔离和配置回归测试

**文件：** tests/unit/test_conversation.py、tests/unit/test_models.py、tests/unit/test_config.py  
**依赖：** T6、T8

**步骤：**

1. 在普通任务、PLAN、DO 和工具续轮中断言 FakeProvider 收到 ProviderRequest。
2. 断言每次请求的动态环境消息会更新，但 session.messages 不包含 system 或 developer 补充文本。
3. 断言规划模式第 1、4 次 Provider 请求分别获得完整和关键规则。
4. 断言取消、Provider 异常、迭代上限和未知工具停止时仍不会提交系统提示或临时用户消息。
5. 断言旧 ProviderConfig 构造和旧 YAML 配置仍能工作。

**验证：**

~~~text
uv run pytest tests/unit/test_conversation.py tests/unit/test_models.py tests/unit/test_config.py -q
~~~

预期：N4、N5 的既有主路径与新增隔离边界同时通过。

## T16：补齐 OpenAI 请求体和缓存集成测试

**文件：** tests/integration/test_openai_sse.py  
**依赖：** T9、T10

**步骤：**

1. 断言稳定 system 消息处于普通历史之前。
2. 断言动态补充消息使用 okcode-system-note 标签，且序列化为 developer 或兼容降级 system，不会成为 user 消息。
3. 断言 prompt_cache=false 时无 prompt_cache_key；开启后 key 只由稳定内容决定。
4. 断言工具定义仍完整发送，流式工具调用解析不回归。
5. 断言 cached_tokens 映射和缺失字段行为。

**验证：**

~~~text
uv run pytest tests/integration/test_openai_sse.py -q
~~~

预期：OpenAI 请求序列化、流式工具调用和缓存观测全部通过。

## T17：补齐 Anthropic 请求体和缓存集成测试

**文件：** tests/integration/test_anthropic_sse.py  
**依赖：** T11、T12

**步骤：**

1. 断言 system 参数是稳定 system block 与动态 blocks 的有序数组。
2. 断言仅稳定 block 和按策略选中的最后一个工具带 cache_control。
3. 断言工具结果仍映射为 user content 中的 tool_result blocks。
4. 断言 planning 和普通任务的 system 补充都不写入 messages 历史。
5. 断言 cache_read_input_tokens、cache_creation_input_tokens 的映射和字段缺失行为。

**验证：**

~~~text
uv run pytest tests/integration/test_anthropic_sse.py -q
~~~

预期：Anthropic 的既有 thinking、多工具调用和历史回传测试不回归。

## T18：准备人工对比场景

**文件：** tests/manual/phase4_prompt_scenarios.md  
**依赖：** T13、T14、T16、T17

**步骤：**

1. 写明运行前置条件：测试工作区、支持缓存 usage 的 Provider、是否开启 prompt_cache。
2. 为读文件前置、代码搜索、文件编辑、/plan、动态环境变化和缓存观测分别给出输入、步骤、应观察到的行为和记录栏。
3. 在每个场景中区分“模型行为”与“API 用量字段”，不把人工判断和自动断言混为一谈。
4. 明确真实 MCP、项目指令和自动评分器不属于场景前置条件。

**验证：**

~~~text
rg -n "读文件|代码搜索|文件编辑|/plan|环境|缓存" tests/manual/phase4_prompt_scenarios.md
~~~

预期：六类场景均有可手工运行的输入和观察点。

## T19：执行回归、格式检查和验收记录

**文件：** docs/phase4_prompt_system/checklist.md  
**依赖：** T13、T14、T15、T16、T17、T18

**步骤：**

1. 运行提示模块、会话和 Provider 集成测试。
2. 运行完整测试套件。
3. 运行 Ruff 格式检查和静态检查。
4. 按 checklist 逐条记录命令、实际输出和人工场景状态。
5. 若任一检查失败，修复后从相关任务验证命令重新开始。

**验证：**

~~~text
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
~~~

预期：全部命令通过，checklist 中每项都有可复现证据。

## 实施补充：终端缓存摘要

在 Provider 已解析缓存字段并写入 TokenUsage 后，扩展终端的 TokenUsageReported 展示：真实字段可用时输出缓存读取和缓存写入，字段缺失时保持原有不可用语义。对应终端单元测试已加入并通过。

## 执行顺序

~~~text
T1 -> T2 -> T3 -> T4
                -> T5
T1 -> T6 -> T7
T3 + T4 + T5 + T6 + T7 -> T8
T8 -> T9 -> T10 -> T16
T8 -> T11 -> T12 -> T17
T3 + T5 + T6 -> T13
T4 + T5 -> T14
T6 + T8 -> T15
T13 + T14 + T16 + T17 -> T18 -> T19
~~~
