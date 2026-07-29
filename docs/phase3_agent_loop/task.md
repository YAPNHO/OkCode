# OkCode 第三阶段：Agent Loop Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `src/okcode/models.py` | 多工具会话消息、Token 用量和 Agent 事件模型 |
| 修改 | `src/okcode/conversation.py` | Agent Loop、批次调度、停止条件、Plan Mode 和原子提交 |
| 修改 | `src/okcode/tools/models.py` | 工具安全类别和工具定义扩展 |
| 修改 | `src/okcode/tools/registry.py` | 按安全类别过滤工具定义 |
| 修改 | `src/okcode/tools/files.py` | 文件工具的安全类别声明 |
| 修改 | `src/okcode/tools/search.py` | 搜索工具的安全类别声明 |
| 修改 | `src/okcode/tools/command.py` | 命令工具的安全类别声明 |
| 修改 | `src/okcode/providers/openai.py` | 多工具调用、Token 用量和多结果历史序列化 |
| 修改 | `src/okcode/providers/anthropic.py` | 多 tool_use、Token 用量和多结果历史序列化 |
| 修改 | `src/okcode/terminal.py` | 新 Agent 事件渲染 |
| 修改 | `src/okcode/app.py` | 正常结束和停止事件的 REPL 集成 |
| 修改 | `tests/fakes.py` | 按 Provider 请求分段的可控流和调度测试替身 |
| 修改 | `tests/unit/test_models.py` | 多工具消息和事件模型测试 |
| 修改 | `tests/unit/test_tools_registry.py` | 安全类别和只读过滤测试 |
| 修改 | `tests/unit/test_conversation.py` | Agent Loop、停止条件、批次调度和 Plan Mode 测试 |
| 修改 | `tests/unit/test_terminal.py` | 新事件渲染测试 |
| 修改 | `tests/unit/test_app.py` | REPL 停止/提示行为测试 |
| 修改 | `tests/integration/test_openai_sse.py` | OpenAI 多工具调用、用量和历史回写测试 |
| 修改 | `tests/integration/test_anthropic_sse.py` | Anthropic 多工具调用、用量和历史回写测试 |
| 修改 | `tests/integration/test_tool_turn.py` | 多轮真实工具链路与协议历史测试 |

## T1：扩展多工具会话消息

**文件：** `src/okcode/models.py`、`tests/unit/test_models.py`  
**依赖：** 无

**步骤：**

1. 将助手消息的单个工具调用改为有序工具调用集合，将工具消息的单个工具结果改为有序工具结果集合。
2. 调整角色校验：用户消息只允许文本；助手消息允许正式文本、工具调用集合或二者同时存在；工具消息只允许非空工具结果集合。
3. 为单工具场景保留清晰、低迁移成本的访问方式，随后逐步迁移调用方到集合语义。
4. 覆盖零、一个、多个工具调用及多个工具结果的合法/非法组合测试。

**验证：** `uv run pytest tests/unit/test_models.py` 通过。

## T2：定义 Token 用量与 Agent 事件

**文件：** `src/okcode/models.py`、`tests/unit/test_models.py`  
**依赖：** T1

**步骤：**

1. 定义 Provider 可用/不可用的 Token 用量模型。
2. 扩展流完成事件，使其可以携带 Token 用量。
3. 定义进度、工具调用请求、Token 用量报告和 Agent 停止事件。
4. 扩展统一轮次事件联合类型，保留已有文本、思考和工具执行事件。
5. 测试事件字段、不可用用量表达和单工具兼容场景。

**验证：** `uv run pytest tests/unit/test_models.py` 通过。

## T3：为工具定义增加安全类别

**文件：** `src/okcode/tools/models.py`、`src/okcode/tools/registry.py`、`tests/unit/test_tools_registry.py`  
**依赖：** 无

**步骤：**

1. 定义只读与有副作用两种工具安全类别。
2. 在工具定义中加入安全类别字段，新工具默认视为有副作用。
3. 为注册表增加按安全类别过滤工具定义的能力，保持名称排序稳定。
4. 测试默认安全类别、全量定义查询、只读定义查询和排序行为。

**验证：** `uv run pytest tests/unit/test_tools_registry.py` 通过。

## T4：标记六个核心工具的安全类别

**文件：** `src/okcode/tools/files.py`、`src/okcode/tools/search.py`、`src/okcode/tools/command.py`、`tests/unit/test_tools_registry.py`  
**依赖：** T3

**步骤：**

1. 将读文件、找文件、搜代码标记为只读。
2. 将写文件、编辑文件、执行命令标记为有副作用。
3. 断言默认注册表的六个工具名称和安全类别完全符合 Plan 定义。

**验证：** `uv run pytest tests/unit/test_tools_registry.py` 通过。

## T5：升级可控 Provider 和工具测试替身

**文件：** `tests/fakes.py`、`tests/unit/test_conversation.py`  
**依赖：** T1、T2、T3

**步骤：**

1. 让 FakeProvider 为每次 `stream()` 请求返回独立脚本流，记录每轮消息和工具定义。
2. 保留单轮脚本的便捷构造方式，避免无关测试大规模改写。
3. 提供可记录开始、结束和并发度的受控工具替身。
4. 为替身本身增加最小行为测试，确认异常和取消会关闭流。

**验证：** `uv run pytest tests/unit/test_conversation.py -k "fake or successful"` 通过。

## T6：支持 OpenAI 多工具调用、历史和用量

**文件：** `src/okcode/providers/openai.py`、`tests/integration/test_openai_sse.py`  
**依赖：** T1、T2

**步骤：**

1. 按工具调用 index 累计流式名称、ID 和参数片段，并在流结束时按 index 生成完整调用集合。
2. 移除关闭并行工具调用的请求参数，使模型可以返回多个调用。
3. 在请求中启用可用的流式用量返回，并把最终用量放入流完成事件；未提供时标记不可用。
4. 序列化助手消息的全部工具调用；把一个工具消息中的多个结果展开为 OpenAI 所需的多个工具消息。
5. 将“只取第一个工具调用”的旧测试改为多调用、片段拼接、用量和历史对应测试。

**验证：** `uv run pytest tests/integration/test_openai_sse.py` 通过。

## T7：支持 Anthropic 多工具调用、历史和用量

**文件：** `src/okcode/providers/anthropic.py`、`tests/integration/test_anthropic_sse.py`  
**依赖：** T1、T2

**步骤：**

1. 收集最终消息中全部 `tool_use` 块及其 JSON 输入片段，按内容顺序生成调用集合。
2. 移除禁止并行工具使用的请求设置。
3. 保存包含所有工具使用块的 Provider 私有状态，保证下一轮请求合法。
4. 将一个工具消息中的多个执行结果序列化为同一用户消息内的多个 `tool_result` 块。
5. 从流开始和流结束增量中汇总可用 Token 用量；未提供时标记不可用。
6. 将“只取第一个工具调用”的旧测试改为多调用、用量和历史对应测试。

**验证：** `uv run pytest tests/integration/test_anthropic_sse.py` 通过。

## T8：实现单轮流式收集与事件转发

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T1、T2、T5

**步骤：**

1. 提取单次 Provider 调用的流式收集逻辑：实时转发文本/思考增量，同时缓存唯一完整完成消息。
2. 验证完成事件唯一、完成后无额外增量、完成消息角色正确。
3. 在每轮完成后发出 Token 用量报告事件。
4. 保持 Provider 异常和取消向上冒泡，确保调用方可以放弃临时上下文。

**验证：** `uv run pytest tests/unit/test_conversation.py -k "stream or delta or exception"` 通过。

## T9：实现多工具批次调度

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T3、T4、T5、T8

**步骤：**

1. 按原始工具调用顺序把连续只读调用组成并发批次，把每个有副作用调用组成单元素串行批次。
2. 每个调用执行前发出工具调用请求和工具开始事件，执行后发出工具完成事件。
3. 对只读批次并发等待，但按原始调用索引重排结果。
4. 把同一助手消息的全部工具结果作为一个工具消息加入临时上下文。
5. 测试只读工具的可观测并发、有副作用工具的严格串行、混合批次屏障和结果回写顺序。

**验证：** `uv run pytest tests/unit/test_conversation.py -k "batch or parallel or serial"` 通过。

## T10：实现 ReAct 循环和停止条件

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T8、T9

**步骤：**

1. 用最多 12 次模型请求的循环替代现有单轮工具流程。
2. 每轮模型返回工具调用时执行批次、更新临时上下文并继续；返回非空最终文本时一次性提交所有临时消息。
3. 统计连续未知工具结果，达到 2 次时发出停止事件且不再请求模型。
4. 到达迭代上限时发出停止事件且不发起第 13 次请求。
5. 取消、流异常、未知工具停止和迭代上限停止都不提交本轮历史；工具副作用不尝试回滚。
6. 覆盖多轮成功、工具失败后继续、未知工具、迭代上限、异常回滚和取消回滚测试。

**验证：** `uv run pytest tests/unit/test_conversation.py` 通过。

## T11：实现 Plan Mode 命令与会话计划状态

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`  
**依赖：** T4、T10

**步骤：**

1. 解析 `/plan <任务>`、`/do` 和普通输入；`/exit` 仍由应用层处理。
2. `/plan` 只传入只读工具定义，成功最终文本后更新当前会话最近计划。
3. `/plan` 取消、异常或非成功停止时保留旧计划。
4. `/do` 从最近计划生成执行请求并传入全量工具定义。
5. `/do` 没有已保存计划时发出明确停止/提示事件，不调用 Provider。
6. 测试工具可见范围、计划覆盖/保留、`/do` 请求内容和无计划分支。

**验证：** `uv run pytest tests/unit/test_conversation.py -k "plan or do"` 通过。

## T12：渲染新增 Agent 事件

**文件：** `src/okcode/terminal.py`、`tests/unit/test_terminal.py`  
**依赖：** T2

**步骤：**

1. 为工具调用请求、进度、Token 用量和停止事件添加简洁的终端展示。
2. 保持文本流式输出、思考区闭合和工具结果摘要的现有展示行为。
3. 对不可用 Token 用量显示明确状态，不输出虚构数字。
4. 测试各事件的可见文本及每轮结束后 UI 状态复位。

**验证：** `uv run pytest tests/unit/test_terminal.py` 通过。

## T13：接入 REPL 正常停止路径

**文件：** `src/okcode/app.py`、`tests/unit/test_app.py`  
**依赖：** T10、T11、T12

**步骤：**

1. 保持 `/exit`、空输入、Provider 错误和 KeyboardInterrupt 的既有语义。
2. 让 Agent 非成功停止事件走正常事件消费和轮次收尾，而不是被误报为 Provider 错误。
3. 覆盖 `/do` 无计划、达到循环停止条件后继续输入、取消后继续输入的 REPL 场景。

**验证：** `uv run pytest tests/unit/test_app.py` 通过。

## T14：补齐端到端兼容与全量质量检查

**文件：** `tests/integration/test_tool_turn.py`、`tests/integration/test_openai_sse.py`、`tests/integration/test_anthropic_sse.py`、`tests/fakes.py`  
**依赖：** T6、T7、T10、T11、T13

**步骤：**

1. 将现有单工具集成测试调整为“工具调用 -> 工具结果 -> 最终文本”的完整两轮流程。
2. 添加多工具协议历史测试，验证 OpenAI 和 Anthropic 的所有调用 ID 与工具结果一一对应。
3. 添加 `/plan` 调研并保存、`/do` 多轮执行后最终答复的端到端脚本。
4. 运行全量测试和 Ruff，修复本阶段引入的格式、导入或类型问题。

**验证：** `uv run pytest`、`uv run ruff check .` 均通过。

## 执行顺序

```text
T1 -> T2
T3 -> T4
T1 + T2 + T3 -> T5
T1 + T2 -> T6
T1 + T2 -> T7
T1 + T2 + T5 -> T8
T3 + T4 + T5 + T8 -> T9
T8 + T9 -> T10
T4 + T10 -> T11
T2 -> T12
T10 + T11 + T12 -> T13
T6 + T7 + T10 + T11 + T13 -> T14
```
