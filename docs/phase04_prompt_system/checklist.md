# OkCode 第四阶段：系统提示与缓存策略 Checklist

> 每一项必须通过运行命令、检查可控请求对象或完成明确人工操作来验收。除非项目代码已经实现，不得预先勾选。

## 提示结构

- [x] 固定系统提示按“身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出”顺序生成，模块之间恰有一个空行。
  验证：运行 test_prompt_builder.py 中的固定模块顺序断言，检查 debug_full_prompt。

- [x] 环境信息位于七个固定模块之后，且不属于 stable_system。
  验证：构造含工作区、平台、日期和工具列表的 PromptBuildContext，断言环境文本仅出现在 dynamic_system 和完整调试文本尾部。

- [x] 自定义指令、已激活 Skill、长期记忆在环境信息之后按固定顺序追加。
  验证：分别提供三个可选模块，断言完整调试文本顺序；分别省略它们，断言没有空标题或占位符。

- [x] 只改变日期、工作区状态、用户输入或运行时补充消息时，stable_system 和 cache_key 保持字节级一致。
  验证：用两份仅动态字段不同的 PromptBuildContext 运行 PromptBuilder 并比较结果。

- [x] 改变固定提示模块文本或模型可见工具描述时，cache_key 随之改变。
  验证：替换一条稳定模块内容或工具描述后重新构建，断言两个 key 不相等。

## 工具约束

- [x] 全局“工具使用”模块同时包含专用工具优先、编辑前先读、禁止伪造工具结果、副作用谨慎四条规则。
  验证：在固定模块文本中逐条匹配关键词。

- [x] read_file、find_files、search_code 的模型描述均包含对应的读取或检索优先规则。
  验证：运行 test_prompt_tools.py，检查增强后 description。

- [x] write_file 和 edit_file 的模型描述均要求编辑前读取或确认目标内容。
  验证：运行 test_prompt_tools.py，检查增强后 description。

- [x] run_command 的模型描述说明验证/诊断目的和副作用谨慎原则。
  验证：运行 test_prompt_tools.py，检查增强后 description。

- [x] 工具描述增强不改变 name、input_schema、timeout_seconds、safety 或工具排序。
  验证：将增强前后 ToolDefinition 逐字段比较，并断言执行器仍以原注册表工具工作。

## 补充指令与任务模式

- [x] 环境、运行时和任务模式补充指令使用 okcode-system-note 特殊标签。
  验证：构造每类 SystemInstruction，断言渲染结果具有开始和结束标签及 kind 属性。

- [x] 系统级补充指令不会以普通 user 消息提交，也不会写入 session.messages。
  验证：用 FakeProvider 执行普通任务、/plan、工具续轮和异常回滚路径，检查 ProviderRequest 与最终历史。

- [x] /plan 的第一次模型迭代收到完整规划规则。
  验证：调用 TaskModeInstructionPlanner 或用 FakeProvider 发起 /plan，断言 iteration=1 的补充文本含完整只读规划约束。

- [x] /plan 的第 4、8 次模型迭代收到关键规则，第 2、3、5 次只收到精简提醒。
  验证：在可控多工具调用脚本中检查每次 ProviderRequest 的 task_mode 补充文本。

- [x] /do 收到执行已保存计划、先读后改、验证后报告的精简规则，不重复完整规划规则。
  验证：先用 /plan 保存计划，再调用 /do，检查第二次请求的 SystemInstruction。

- [x] 普通任务不注入规划或执行计划补充规则。
  验证：用普通用户输入调用 ConversationSession，断言 dynamic_system 中没有 task_mode kind。

## OpenAI Provider

- [x] OpenAI 请求把稳定系统提示放在普通历史之前，把动态补充放在稳定块之后。
  验证：运行 tests/integration/test_openai_sse.py，检查录制请求的 messages 顺序。

- [x] OpenAI 动态补充消息带 okcode-system-note 标签，序列化为 developer；兼容降级时为 system，绝不为 user。
  验证：分别运行原生和兼容模式请求体测试。

- [x] prompt_cache=false 时，OpenAI 兼容请求不携带 prompt_cache_key、prompt_cache_retention 或其他新增缓存路由字段。
  验证：检查关闭缓存的录制请求 JSON。

- [x] prompt_cache=true 且配置确认支持时，OpenAI 请求携带由稳定内容生成的 prompt_cache_key。
  验证：比较两次仅环境不同的请求 key 相同；改变固定模块或工具描述后 key 不同。

- [x] OpenAI usage 中的 prompt_tokens_details.cached_tokens 被映射为 TokenUsage.cache.read_tokens。
  验证：运行包含 cached_tokens 的 SSE 替身测试。

- [x] OpenAI usage 缺少 cached_tokens 时，TokenUsage.cache.available 为 false，write_tokens 为 None，不估算缓存命中。
  验证：运行 usage 缺失字段的 SSE 替身测试。

- [x] OpenAI 既有 thinking、流式文本、流式工具调用和工具结果历史回传测试仍通过。
  验证：运行 uv run pytest tests/integration/test_openai_sse.py -q。

## Anthropic Provider

- [x] Anthropic system 参数是“稳定 system text block 在前，动态补充 blocks 在后”的有序数组。
  验证：运行 tests/integration/test_anthropic_sse.py，检查录制请求的 system 字段。

- [x] 开启 prompt_cache 时，稳定 system block 带 cache_control；动态 system blocks 不带 cache_control。
  验证：检查开启缓存的录制请求 JSON。

- [x] Anthropic 工具定义稳定排序；缓存策略启用工具缓存时，只有按策略选中的稳定工具带 cache_control。
  验证：构造多个工具定义并检查请求 tools 数组。

- [x] Anthropic 工具结果仍作为 user content 中的 tool_result blocks 回传。
  验证：运行已有多工具调用和历史序列化集成测试。

- [x] Anthropic usage 中的 cache_read_input_tokens 和 cache_creation_input_tokens 分别映射为 TokenUsage.cache.read_tokens、write_tokens。
  验证：运行包含两个字段的 SSE 替身测试。

- [x] Anthropic usage 缺少两个缓存字段时，TokenUsage.cache.available 为 false，不填估算值。
  验证：运行 usage 缺失字段的 SSE 替身测试。

- [x] Anthropic 既有 extended thinking、流式文本、多工具调用和 provider_state 历史回传测试仍通过。
  验证：运行 uv run pytest tests/integration/test_anthropic_sse.py -q。

## 会话与回归

- [x] ConversationSession 每次 Agent Loop 迭代都重新构造动态环境和任务模式补充，但不污染已提交历史。
  验证：在有工具续轮的 FakeProvider 脚本中比较连续 ProviderRequest 的 prompt.dynamic_system 和 session.messages。

- [x] 正常文本任务、工具调用完成后的最终回答、/plan 保存计划、/do 执行计划仍按原子规则提交用户、助手和工具消息。
  验证：运行 tests/unit/test_conversation.py -q。

- [x] Provider 异常、取消、迭代上限和未知工具停止时，不提交本轮普通消息，也不泄露系统提示。
  验证：运行对应 rollback、cancellation、iteration、unknown tool 测试。

- [x] 旧 ProviderConfig 构造和未包含 prompt_cache 的 YAML 配置仍可加载。
  验证：运行 tests/unit/test_models.py 和 tests/unit/test_config.py。

- [x] prompt_cache 不是布尔值时，配置加载显示简洁且不含 API Key 的配置错误。
  验证：用非法 YAML fixture 运行配置测试。

- [x] 终端默认不打印 stable_system、debug_full_prompt、原始 Provider 请求或 API Key。
  验证：使用唯一测试密钥运行欢迎、正常、错误和用量展示路径，检查终端捕获文本。

## 自动化检查

- [x] 新增提示构建、模式、工具增强单元测试全部通过。
  验证：uv run pytest tests/unit/test_prompt_builder.py tests/unit/test_prompt_modes.py tests/unit/test_prompt_tools.py -q。

- [x] 新增模型、配置、会话回归测试全部通过。
  验证：uv run pytest tests/unit/test_models.py tests/unit/test_config.py tests/unit/test_conversation.py -q。

- [x] OpenAI 和 Anthropic 的集成替身测试全部通过。
  验证：uv run pytest tests/integration/test_openai_sse.py tests/integration/test_anthropic_sse.py -q。

- [x] 完整自动化测试套件通过。
  验证：uv run pytest -q。

- [x] 代码格式符合项目规范。
  验证：uv run ruff format --check .。

- [x] 静态检查通过。
  验证：uv run ruff check .。

## 人工场景

- [ ] 场景 1：要求解释指定文件内容时，模型先调用 read_file，再基于工具结果回答。
  验证：按 tests/manual/phase4_prompt_scenarios.md 记录工具调用顺序与最终回答。

- [ ] 场景 2：要求定位函数、类或文本引用时，模型优先调用 search_code 或 find_files，不凭空给出路径。
  验证：记录第一次工具调用和路径/行号证据。

- [ ] 场景 3：要求修改已有文件时，模型先读取或搜索确认目标内容，再调用 edit_file 或 write_file。
  验证：记录完整工具调用顺序，确认编辑前存在对应读取证据。

- [ ] 场景 4：使用 /plan 调研任务时，模型只获得只读工具，并输出可执行计划；随后 /do 能使用保存计划和全量工具。
  验证：记录两次请求的可见工具、模式补充规则和 SavedPlan。

- [ ] 场景 5：连续两轮仅改变日期或环境状态时，稳定提示摘要不变，动态补充文本变化。
  验证：记录两次 ProviderRequest 的 cache_key、stable_system 和 dynamic_system。

- [ ] 场景 6：使用支持缓存 usage 的 Provider 连续运行相同稳定前缀请求时，用量事件显示真实 cache read 或 cache write 字段；字段不存在时明确不可用。
  验证：记录原始 usage 摘要、归一化缓存用量和 Provider 名称。

## 验收报告

实现完成后按下列格式补充本节：

~~~text
### 通过（N/M）
- [x] 提示构建、任务模式、工具描述增强：对应单元测试通过。
- [x] 会话历史隔离与配置兼容：tests/unit 相关测试通过。
- [x] OpenAI 与 Anthropic 请求序列化、缓存 usage：tests/integration 相关测试通过。
- [x] 完整回归：uv run pytest -q，106 passed。
- [x] 代码质量：uv run ruff format --check . 和 uv run ruff check . 均通过。

### 未通过（如有）
- [ ] 六个真实 Provider 人工场景：本阶段已准备场景文档，尚未使用真实 API Key 运行定性对比。

### 端到端
- [x] 可控 Provider 链路：普通任务、/plan、/do、工具续轮、OpenAI 和 Anthropic 请求体均由自动化测试覆盖。
~~~
