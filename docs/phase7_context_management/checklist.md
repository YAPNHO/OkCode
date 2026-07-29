# OkCode 第七阶段：上下文管理 Checklist

> 所有项目均通过运行测试或观察可记录的请求、历史和文件状态验证。

## 实现完整性

- [ ] 单个工具结果恰为 `50_000` 字符时仍以原文进入历史，超过该值时完整稳定 JSON 写入会话隔离的工作区文件，历史只保留预览和可读取路径。
  验证：运行 `uv run pytest tests/unit/test_context_artifacts.py -q`，观察边界、路径和文件内容断言通过。

- [ ] 一条工具消息包含 `42K、38K、45K、40K、44K` 五个结果时，仅 `45K` 项被外置，其余四项原文保留，替换后合计不超过 `200_000` 字符。
  验证：运行 `uv run pytest tests/unit/test_context_manager.py -q`，观察聚合选择和稳定顺序断言通过。

- [ ] Token 估算优先使用最近正常请求的真实 Usage 锚点，只以字符数估算变化；Usage 缺失时退化为全文字符估算。
  验证：运行 `uv run pytest tests/unit/test_context_manager.py -q`，观察锚点、增量和回退用例通过。

- [ ] 估算完整正常请求超过 `167_000` Token 时，在正常 Provider 调用前执行摘要；成功后保留尾部不少于约 `10_000` Token 或 5 条消息中的较大者，并且不拆分工具调用与结果。
  验证：运行 `uv run pytest tests/unit/test_conversation.py -q`，观察 Provider 请求顺序和保留历史断言通过。

- [ ] 摘要请求的工具列表为空、缓存关闭，提示明确禁止工具调用，并要求分析草稿与正式摘要分区。
  验证：运行 `uv run pytest tests/unit/test_context_summary.py -q`，观察请求字段和提示文本断言通过。

- [ ] 正式摘要只保留九个规定部分，草稿被丢弃；第六部分的所有用户消息逐字来自程序维护的原文列表，当前工作部分存在且最详细。
  验证：运行 `uv run pytest tests/unit/test_context_summary.py -q`，观察标题校验、草稿剔除和用户消息替换断言通过。

- [ ] 摘要成功后，正常请求动态系统补充同时包含结构化摘要和“需要细节请重新读取文件”的边界消息，且二者不进入稳定缓存前缀。
  验证：运行 `uv run pytest tests/unit/test_prompt_builder.py -q`，观察动态提示与缓存键断言通过。

- [ ] `/compact` 在低于 `167_000` Token 时仍立即调用摘要；没有可摘要历史时不调用 Provider 并报告无操作状态。
  验证：运行 `uv run pytest tests/unit/test_conversation.py -q`，观察手动命令两个分支通过。

- [ ] 摘要或外置失败不替换原历史；连续三次摘要失败后熔断，第四次自动或手动请求均不再调用摘要 Provider。
  验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_context_manager.py -q`，观察历史、调用次数和停止原因断言通过。

## 集成

- [ ] `ConversationSession` 在工具执行后、下一次模型请求前完成轻量外置；后续模型请求读取到预览和路径，而不是原始大结果。
  验证：运行 `uv run pytest tests/integration/test_tool_turn.py -q`，观察 Fake Provider 捕获的第二次请求。

- [ ] 自动摘要由会话层直接调用既有 Provider，不向终端转发摘要的 ThinkingDelta 或 TextDelta；正常响应继续沿用既有流式事件和 Token 展示。
  验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_terminal.py -q`，观察事件序列和终端输出断言通过。

- [ ] CLI 以当前工作区创建上下文管理器，外置文件位于 `.okcode/context/`，且普通对话、计划、权限与 MCP 装配路径不因新增依赖改变。
  验证：运行 `uv run pytest tests/unit/test_cli.py tests/unit/test_app.py -q`，观察装配和既有行为断言通过。

- [ ] 自动摘要无法选择安全前缀或正式摘要格式不合法时，本轮停止而不发送可能超窗的正常请求。
  验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_context_summary.py -q`，观察正常 Provider 请求数和停止原因断言通过。

## 编译与测试

- [ ] 上下文管理模块的全部单元测试通过。
  验证：`uv run pytest tests/unit/test_context_artifacts.py tests/unit/test_context_manager.py tests/unit/test_context_summary.py -q` 返回成功。

- [ ] 全部项目测试通过，包含 Provider SSE、工具、权限、提示与 MCP 回归。
  验证：`uv run pytest -q` 返回成功。

- [ ] 格式与静态检查通过。
  验证：`uv run ruff format --check .` 和 `uv run ruff check .` 均返回成功。

## 端到端场景

- [ ] 场景 1：模型在一轮中请求五个工具，结果大小为 `42K、38K、45K、40K、44K` 字符。
  预期：第二次模型请求仅包含 `45K` 结果的预览和 `.okcode/context/` 路径；读取该路径获得完整原结果，其他四项保持原文。

- [ ] 场景 2：会话估算超过 `167K` Token 后用户继续任务。
  预期：先发起无工具摘要请求，模型收到的下一次正常请求包含九段正式摘要、边界消息和最近原文；原工具协议配对仍然有效。

- [ ] 场景 3：用户在短会话输入 `/compact`。
  预期：立即发起一次无工具摘要请求；不会因当前远低于阈值而跳过。

- [ ] 场景 4：摘要连续三次由 Provider 失败。
  预期：每次失败后历史不变；第三次显示熔断，第四次压缩请求不再访问 Provider。
