# OkCode 第二阶段：工具系统 Checklist

> 每项均通过运行测试、检查受控请求或观察可见行为验证，不依赖逐行阅读实现代码。全部通过后才可认定本章完成。

## 工具契约与注册

- [x] 六项工具均以唯一名称、面向模型的描述、对象型 JSON Schema 和正超时值登记在默认注册表中。
  验证：运行 `uv run pytest tests/unit/test_tools_registry.py -q`，观察注册表恰含 `read_file`、`write_file`、`edit_file`、`run_command`、`find_files`、`search_code`，并拒绝重名。

- [x] OpenAI 与 Anthropic Provider 能从同一组工具定义生成各自协议合法的工具声明。
  验证：运行 `uv run pytest tests/integration/test_openai_sse.py tests/integration/test_anthropic_sse.py -q -k "tool_schema"`，检查捕获请求中的 OpenAI `function.parameters` 和 Anthropic `input_schema` 均等于工具 Schema。

- [x] 未知工具、无效 JSON、缺少参数、类型错误和额外字段均变为带 `success=false`、`error_code` 和可读 `content` 的结构化结果。
  验证：运行 `uv run pytest tests/unit/test_tools_executor.py -q -k "unknown or json or schema"`，检查不出现未处理异常。

## 核心工具

- [x] `read_file` 读取工作区内的 UTF-8 文本，返回相对路径和文件内容；文件不存在、目录目标、无效编码与大文件均产生可诊断或截断结果。
  验证：运行 `uv run pytest tests/unit/test_tools_files.py -q -k "read"`。

- [x] `write_file` 能创建缺失父目录并完整写入或覆盖文本文件，结果包含相对路径和写入摘要。
  验证：运行 `uv run pytest tests/unit/test_tools_files.py -q -k "write"`，检查测试工作区中文件内容、父目录和摘要均正确。

- [x] `edit_file` 仅在原文恰好出现一次时替换；零次或多次匹配时返回不同错误码，原文件字节内容完全不变。
  验证：运行 `uv run pytest tests/unit/test_tools_files.py -q -k "edit"`，检查成功、零次和多次匹配三种情形。

- [x] `find_files` 按模式稳定返回工作区相对文件路径；`search_code` 返回匹配的路径、行号和文本行，并支持起始目录和文件模式限制。
  验证：运行 `uv run pytest tests/unit/test_tools_search.py -q -k "find or search"`，检查递归模式、行号、模式过滤和无匹配场景。

- [x] `run_command` 始终以工作区为当前目录；成功命令保留输出，非零退出返回退出码和 `command_failed`，不被误报为应用错误。
  验证：运行 `uv run pytest tests/unit/test_tools_command.py -q -k "cwd or success or nonzero"`。

## 安全、原子性与资源边界

- [x] 所有文件相关工具拒绝绝对路径、父级回退路径、工作区外路径和解析后越界的符号链接，且不泄漏外部文件内容。
  验证：运行 `uv run pytest tests/unit/test_tools_workspace.py tests/unit/test_tools_files.py tests/unit/test_tools_search.py -q -k "outside or symlink or parent or absolute"`。

- [x] 文件写入和唯一替换在写入异常时不留下部分内容或临时文件；唯一匹配确认前不发生任何写入。
  验证：运行 `uv run pytest tests/unit/test_tools_files.py -q -k "atomic or rollback or edit"`，比较失败前后的目标文件字节内容并检查临时目录。

- [x] 超量文件、搜索和命令输出不会无限进入结果；结果带 `truncated=true` 和可读截断提示。
  验证：运行 `uv run pytest tests/unit/test_tools_executor.py tests/unit/test_tools_files.py tests/unit/test_tools_search.py tests/unit/test_tools_command.py -q -k "truncat"`。

- [x] 超时命令被终止并回收；结果使用 `timeout` 错误码，测试结束后没有残留子进程。
  验证：运行 `uv run pytest tests/unit/test_tools_command.py -q -k "timeout"`。

## OpenAI 协议集成

- [x] OpenAI 请求包含全部工具定义，且 `parallel_tool_calls=False`；未启用工具时不破坏第一阶段 thinking 请求规则。
  验证：运行 `uv run pytest tests/integration/test_openai_sse.py -q -k "tool_request or thinking"`，检查捕获请求参数。

- [x] OpenAI SSE 中同一工具调用的 ID、名称和 `arguments` 被拆成多个增量时，系统在流完成后只还原一个调用，并且只执行一次。
  验证：运行 `uv run pytest tests/integration/test_openai_sse.py -q -k "tool_fragment"`，检查执行器调用次数为 `1`。

- [x] OpenAI 返回多个工具调用、缺失 ID/名称或未完成参数时，本轮报流错误、不执行工具且不提交历史。
  验证：运行 `uv run pytest tests/integration/test_openai_sse.py tests/unit/test_conversation.py -q -k "multiple_tool or incomplete_tool or missing_tool"`。

- [x] 下一次 OpenAI 请求中的历史包含相邻的 assistant `tool_calls` 和匹配 ID 的 `role="tool"` 结果消息；工具成功与失败均满足此规则。
  验证：运行 `uv run pytest tests/integration/test_openai_sse.py -q -k "tool_history"`，检查捕获消息及失败结果 JSON。

## Anthropic 协议集成

- [x] Anthropic 请求包含全部工具定义，并设置 `tool_choice={"type":"auto","disable_parallel_tool_use":true}`。
  验证：运行 `uv run pytest tests/integration/test_anthropic_sse.py -q -k "tool_request"`，检查捕获请求参数。

- [x] Anthropic SSE 的 `tool_use` 块和多个 `input_json.partial_json` 分片被完整还原为一次调用，并且只执行一次。
  验证：运行 `uv run pytest tests/integration/test_anthropic_sse.py -q -k "input_json or tool_fragment"`。

- [x] Anthropic 返回多个工具块、无效/未完成输入、缺失 ID/名称或停止原因不一致时，本轮报流错误、不执行工具且不提交历史。
  验证：运行 `uv run pytest tests/integration/test_anthropic_sse.py tests/unit/test_conversation.py -q -k "multiple_tool or incomplete_tool or invalid_tool"`。

- [x] 下一次 Anthropic 请求重放 assistant 的 `tool_use` 块，并紧邻发送 user `tool_result` 块；失败结果设置 `is_error=true`，thinking 私有状态仍可保留。
  验证：运行 `uv run pytest tests/integration/test_anthropic_sse.py -q -k "tool_history"`。

## 会话与终端行为

- [x] 普通文本回合保持第一阶段行为：文本和思考增量照常显示，并原子提交用户消息和正式 assistant 回答。
  验证：运行 `uv run pytest tests/unit/test_conversation.py tests/unit/test_terminal.py -q -k "successful_turn or thinking or answer"`。

- [x] 工具调用回合按“开始状态 → 工具完成摘要 → 返回提示符”呈现；详细工具 JSON 不直接输出到终端。
  验证：运行 `uv run pytest tests/unit/test_terminal.py tests/unit/test_app.py -q -k "tool"`，检查终端捕获输出和 `finish_turn()` 调用。

- [x] 一次工具调用后，系统提交 `[user, assistant tool call, tool result]` 并停止；本轮不会向 Provider 发起第二次请求。
  验证：运行 `uv run pytest tests/unit/test_conversation.py -q -k "single_tool or no_second_request"`，检查 Provider 请求次数为 `1`。

- [x] 工具成功与工具失败均提交成对调用/结果历史；Provider 流错误、取消或不合法完成事件则整轮回滚，历史不存在孤立工具消息。
  验证：运行 `uv run pytest tests/unit/test_conversation.py -q -k "tool_success or tool_failure or rollback"`。

- [x] 工具异常、命令失败和超时后，REPL 显示安全摘要并继续接收下一次输入，不输出原始堆栈或敏感值。
  验证：运行 `uv run pytest tests/unit/test_app.py tests/unit/test_terminal.py -q -k "error or tool_failure"`。

- [x] CLI 从启动时工作目录装配默认六工具、工作区和执行器，同时保持配置失败与 Provider 资源关闭行为。
  验证：运行 `uv run pytest tests/unit/test_cli.py -q`。

## 端到端与回归

- [x] 在受控工作区中，让模型请求 `read_file`：终端显示执行状态，工具结果进入历史，回合恢复输入状态。
  验证：运行 `uv run pytest tests/integration/test_tool_turn.py -q -k "read_file"`，检查读取内容、开始/完成事件和一轮结束状态。

- [x] 在该端到端场景的下一次受控 Provider 请求中，分别验证 OpenAI 和 Anthropic 历史包含合法且完整的工具调用/结果对。
  验证：运行 `uv run pytest tests/integration/test_tool_turn.py -q -k "history"`。

- [x] 所有自动化测试和静态检查通过。
  验证：依次运行 `uv run pytest -q` 和 `uv run ruff check src tests`，预期均以退出码 `0` 结束。
