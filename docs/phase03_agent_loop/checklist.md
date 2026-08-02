# OkCode 第三阶段：Agent Loop Checklist

> 每一项均通过运行代码或观察行为验证，聚焦已确认 Spec 的系统行为。

## 实现完整性

- [ ] 多工具消息：助手的一轮回复可携带多个工具调用，工具结果可作为同一轮的多个结果保存；单工具场景保持可用。（验证：`uv run pytest tests/unit/test_models.py`）
- [ ] 流式双路收集：文本增量在完成前已被订阅方收到，完成事件提供同一轮完整助手消息和 Token 用量。（验证：`uv run pytest tests/unit/test_conversation.py -k "stream or delta"`）
- [ ] 事件流完整：一次 Agent 运行可观测到文本、工具调用、工具结果、进度和每轮 Token 用量；不可用用量有明确标记。（验证：`uv run pytest tests/unit/test_conversation.py tests/unit/test_terminal.py`）
- [ ] 工具安全分类：读文件、找文件、搜代码为只读；写文件、编辑文件、执行命令为有副作用。（验证：`uv run pytest tests/unit/test_tools_registry.py`）
- [ ] 多工具批次：连续只读工具并发，有副作用工具严格串行，所有结果按模型调用原顺序写回。（验证：`uv run pytest tests/unit/test_conversation.py -k "batch or parallel or serial"`）
- [ ] ReAct 循环：工具结果回写后自动再次调用模型，直到模型给出最终文本。（验证：`uv run pytest tests/unit/test_conversation.py -k "loop or multi"`）
- [ ] 原子提交：仅最终文本成功时提交本轮完整历史；异常、取消和停止条件不提交本轮历史。（验证：`uv run pytest tests/unit/test_conversation.py -k "rollback or commit"`）
- [ ] Plan Mode：`/plan` 只暴露读类工具并保存最终计划；`/do` 复用最近计划并暴露全量工具。（验证：`uv run pytest tests/unit/test_conversation.py -k "plan or do"`）
- [ ] 无计划执行：无已保存计划时 `/do` 不调用 Provider，终端显示明确提示。（验证：`uv run pytest tests/unit/test_conversation.py tests/unit/test_app.py -k "no_plan or plan"`）

## 停止与恢复

- [ ] 正常完成：模型返回非空、无工具调用的正式文本时停止，且不额外请求模型。（验证：`uv run pytest tests/unit/test_conversation.py -k "successful"`）
- [ ] 迭代上限：连续工具调用时最多发起 12 次模型请求，不发起第 13 次，不提交本轮历史。（验证：`uv run pytest tests/unit/test_conversation.py -k "iteration"`）
- [ ] 未知工具：连续两次结果为 `unknown_tool` 时停止，不发起下一轮模型请求，不提交本轮历史。（验证：`uv run pytest tests/unit/test_conversation.py -k "unknown"`）
- [ ] 流异常：Provider 流异常时关闭当前流、不提交本轮历史，下一条用户输入仍可执行。（验证：`uv run pytest tests/unit/test_conversation.py tests/unit/test_app.py -k "exception or error"`）
- [ ] 用户取消：取消工具/模型运行后不提交本轮历史，REPL 回到可输入状态。（验证：`uv run pytest tests/unit/test_app.py -k "interrupt"`）

## Provider 集成

- [ ] OpenAI：多个分片工具调用被正确还原，所有调用与结果在下一轮请求中按 ID 一一对应，并能产生或标记 Token 用量。（验证：`uv run pytest tests/integration/test_openai_sse.py`）
- [ ] Anthropic：多个 `tool_use` 被正确还原，所有结果作为对应 `tool_result` 回写，并能产生或标记 Token 用量。（验证：`uv run pytest tests/integration/test_anthropic_sse.py`）
- [ ] 单工具兼容：现有单工具工具结果历史仍可被两套 Provider 正确序列化。（验证：`uv run pytest tests/integration/test_tool_turn.py`）

## 终端与端到端

- [ ] 终端渲染：文本/思考流式渲染、工具状态摘要、进度、用量和停止提示都可展示，且轮次结束后状态复位。（验证：`uv run pytest tests/unit/test_terminal.py`）
- [ ] 端到端：`/plan` 调研并生成计划，随后 `/do` 自动执行最近计划、经历多轮工具调用并输出最终答复。（验证：`uv run pytest tests/integration/test_tool_turn.py -k "plan"`）
- [ ] 普通对话回归：不调用工具的用户问题仍能生成文本、提交历史并返回下一次输入。（验证：`uv run pytest tests/unit/test_conversation.py tests/unit/test_app.py -k "successful_turn"`）

## 质量检查

- [ ] 全量测试：所有单元与集成测试均通过。（验证：`uv run pytest`）
- [ ] 静态检查：Ruff 无格式、导入或基础语法问题。（验证：`uv run ruff check .`）
