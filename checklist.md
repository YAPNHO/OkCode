# OkCode 第一阶段：验收清单

> 本清单在用户要求直接开发后补充生成。每项以运行结果或可观察行为为准。

## 配置与启动

- [x] YAML 仅接受 `active`、`providers` 和六个供应商字段（证据：`tests/unit/test_config.py` 通过）。
- [x] 无效 YAML、未知字段、重复名称、错误 URL 和无效 active 会在请求前报错（证据：配置单元测试通过）。
- [x] `api_key` 不出现在配置对象表示和 Provider 安全错误中（证据：配置与 Provider 测试通过）。
- [x] `deepseek-v4-pro` 示例配置可加载（证据：`uv run python -c "...load_config(...)"` 输出模型名）。
- [x] 无默认配置时程序只显示配置错误、没有异常堆栈（证据：`uv run python -m okcode` 输出配置错误）。

## Provider 与流式输出

- [x] OpenAI Chat Completions 以自定义 `base_url`、`stream=True` 和 `max_retries=0` 请求（证据：受控 SDK SSE 测试通过）。
- [x] `thinking: true` 时 OpenAI 兼容 Provider 发送 enabled 扩展、不发送 `reasoning_effort`（证据：`test_thinking_request_and_reasoning_content_stream` 通过）。
- [x] `reasoning_content` 与 `content` 分别映射为思考和回答增量（证据：当前 OpenAI SDK mock SSE 测试通过）。
- [x] OpenAI 流缺少终止标记或没有正式回答时不会提交会话（证据：OpenAI 与 Conversation 测试通过）。
- [x] Anthropic `thinking: true` 使用 1024 budget 和 4096 max tokens（证据：Anthropic 请求测试通过）。
- [x] Anthropic thinking/text 可见，signature/redacted thinking 不显示且可原样回传（证据：Anthropic 私有状态测试通过）。
- [x] 两个 Provider 都关闭默认重试、可安全关闭客户端，并将异常转为脱敏错误（证据：Provider 测试通过）。

## 会话与终端

- [x] 已完成轮次原子写入用户消息与助手消息，失败/取消轮次不写入历史（证据：`tests/unit/test_conversation.py` 通过）。
- [x] 同进程历史会随下一轮请求发送；DeepSeek 历史不回传旧 reasoning 内容（证据：OpenAI 历史序列化测试通过）。
- [x] 思考和回答标签每轮各显示一次，模型文本不会被 Rich markup 解释（证据：终端单元测试通过）。
- [x] 空输入不调用 Provider，`/exit` 和 EOF 可以退出，Provider 错误后能继续输入（证据：应用单元测试通过）。
- [x] 生成阶段的 `KeyboardInterrupt` 会显示取消提示并回到输入状态（证据：应用取消恢复测试通过）。

## 质量门禁

- [x] 全部自动测试通过（证据：`uv run pytest -q`，38 passed）。
- [x] Ruff 格式检查通过（证据：`uv run ruff format --check .`）。
- [x] Ruff lint 通过（证据：`uv run ruff check .`）。
- [x] sdist 与 wheel 均可构建（证据：`uv build`）。

## 真实端到端

- [ ] 在 WSL tmux 中通过 DeepSeek `deepseek-v4-pro` 完成两轮真实对话（阻塞：WSL 中未安装 `uv`；需在 WSL 环境准备依赖并安全提供现有 `config.yaml` 中的有效 API Key）。
- [ ] 在真实终端中验证思考、回答、Ctrl+C 取消与 `/exit`（阻塞：同上）。
