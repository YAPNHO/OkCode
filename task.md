# OkCode 第一阶段：任务拆解

> 用户明确要求直接进入任务拆解阶段。本文件以已在对话中确认的技术设计为实现基线；执行实现前，T1 必须先将磁盘上的 `spec.md` 同步到最新的 DeepSeek V4 Pro 决策，消除其中“OpenAI 不接受 thinking”的旧规则。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `spec.md` | 同步 DeepSeek V4 Pro、OpenAI thinking 与验收标准 |
| 新建 | `pyproject.toml`、`uv.lock`、`.gitignore` | 包元数据、依赖锁定与密钥保护 |
| 新建 | `README.md`、`config.example.yaml` | 安装、配置与运行说明 |
| 新建 | `src/okcode/__init__.py`、`__main__.py` | 包和模块入口 |
| 新建 | `src/okcode/models.py`、`errors.py` | 领域模型与安全错误 |
| 新建 | `src/okcode/config.py` | YAML 读取和严格校验 |
| 新建 | `src/okcode/conversation.py` | 进程内历史和原子提交 |
| 新建 | `src/okcode/terminal.py` | 行内输入和流式渲染 |
| 新建 | `src/okcode/app.py`、`cli.py` | REPL 编排、取消和资源生命周期 |
| 新建 | `src/okcode/providers/__init__.py`、`base.py`、`factory.py` | Provider 接口与工厂 |
| 新建 | `src/okcode/providers/openai.py`、`anthropic.py` | OpenAI/DeepSeek 与 Anthropic 适配器 |
| 新建 | `tests/conftest.py`、`tests/fakes.py` | 假 Provider、假终端和公共夹具 |
| 新建 | `tests/helpers/__init__.py`、`tests/helpers/sse.py` | 可控异步 SSE 测试流 |
| 新建 | `tests/unit/test_config.py`、`test_conversation.py`、`test_terminal.py`、`test_app.py`、`test_cli.py` | 单元测试 |
| 新建 | `tests/integration/test_openai_sse.py`、`test_anthropic_sse.py` | SDK + 受控 SSE 集成测试 |

## T1：同步规格基线

**文件：** `spec.md`

**依赖：** 无

**步骤：**

1. 将 DeepSeek `deepseek-v4-pro` 写入背景、目标和真实端到端验收目标。
2. 将 OpenAI 协议的 `thinking: true` 改为允许的能力扩展：请求发送 `thinking.type=enabled`，流式 `reasoning_content` 显示为思考区，且不发送 `reasoning_effort`。
3. 修改“无第三方专属逻辑”和 F6、AC6、AC7 的旧表述：实现按能力字段工作，不按模型名或 `base_url` 硬编码；不支持扩展的服务返回明确请求错误。
4. 明确纯多轮 DeepSeek 对话只回传助手正式 `content`，不回传上一轮 `reasoning_content`。

**验证：** `rg -n "reasoning_content|thinking.type|deepseek-v4-pro" spec.md` 输出对应规则，且不再包含“OpenAI 配置不接受 `thinking: true`”。

## T2：建立项目元数据与依赖锁定

**文件：** `pyproject.toml`、`uv.lock`

**依赖：** T1

**步骤：**

1. 配置 Python `>=3.12`、Hatchling、`src` 包布局和 `okcode = "okcode.cli:main"` 控制台入口。
2. 添加运行依赖：`openai`、`anthropic`、`prompt-toolkit`、`rich`、`PyYAML`。
3. 添加开发依赖：`pytest`、`pytest-asyncio`、`httpx`、`ruff`；配置 pytest asyncio 模式和 Ruff Python 目标版本。
4. 用 uv 解析并锁定依赖。

**验证：** 依次运行 `uv lock`、`uv sync --all-groups`，预期均成功且生成 `uv.lock`。

## T3：建立包骨架与忽略规则

**文件：** `.gitignore`、`src/okcode/__init__.py`、`src/okcode/providers/__init__.py`、`tests/helpers/__init__.py`

**依赖：** T2

**步骤：**

1. 创建 `src/okcode`、`providers`、`tests/unit`、`tests/integration` 和 `tests/helpers` 目录。
2. 忽略 `.venv`、`__pycache__`、pytest/Ruff 缓存、覆盖率文件和本地 `.okcode` 配置目录。
3. 创建最小包初始化文件，确保安装后能导入，而不是依赖工作目录。

**验证：** `uv run python -c "import okcode; print(okcode.__name__)"` 输出 `okcode`。

## T4：定义配置领域模型

**文件：** `src/okcode/models.py`

**依赖：** T3

**步骤：**

1. 定义 `ProviderProtocol`、`ProviderConfig` 与 `AppConfig`。
2. 使用冻结且带 `slots` 的数据类；`api_key` 不得进入 `repr`。
3. 实现经过验证的 `AppConfig.active_provider` 访问器。

**验证：** `uv run python -m compileall -q src/okcode/models.py` 成功；内联构造配置后 `repr` 不含测试密钥。

## T5：定义消息与统一流事件

**文件：** `src/okcode/models.py`

**依赖：** T4

**步骤：**

1. 定义 `Role`、`ChatMessage`、`ThinkingDelta`、`TextDelta` 和 `StreamCompleted`。
2. 将 `provider_state` 设为不可见且不参与比较的可选状态，供 Anthropic 保存私有 content blocks。
3. 定义 `VisibleDelta` 与 `StreamEvent` 联合类型。

**验证：** `uv run python -c "from okcode.models import ChatMessage, ThinkingDelta, TextDelta, StreamCompleted"` 正常退出。

## T6：定义安全错误模型

**文件：** `src/okcode/errors.py`

**依赖：** T5

**步骤：**

1. 定义 `OkCodeError`、`ConfigError`、`ProviderErrorKind` 和 `ProviderError`。
2. 为认证、权限、连接、超时、限流、错误请求、服务端和流中断提供枚举值。
3. `ProviderError` 只持有安全消息、可选状态码与请求 ID，不保存原始响应或认证头。

**验证：** `uv run python -m compileall -q src/okcode/errors.py` 成功。

## T7：实现 YAML 基础读取

**文件：** `src/okcode/config.py`、`tests/unit/test_config.py`

**依赖：** T4、T6

**步骤：**

1. 实现当前工作目录中的默认 `config.yaml` 路径和可注入路径的 `load_config()`。
2. 使用 `yaml.safe_load()`，处理缺失文件、空文件、非映射根节点及 YAML 语法错误。
3. 将解析失败转换为不含密钥且带文件位置的 `ConfigError`。

**验证：** `uv run pytest tests/unit/test_config.py -q -k "missing_file or empty_file or syntax_error or root_type"` 全部通过。

## T8：实现 YAML 字段与类型校验

**文件：** `src/okcode/config.py`、`tests/unit/test_config.py`

**依赖：** T7

**步骤：**

1. 顶层只允许 `active` 和 `providers`，供应商项只允许六个约定字段。
2. 校验必填非空字符串、布尔 `thinking`、协议枚举、HTTP(S) `base_url`、唯一 `name` 和有效 `active`。
3. 拒绝未知字段、类型错误、重复名称和不存在的 active 引用。

**验证：** `uv run pytest tests/unit/test_config.py -q -k "unknown_field or invalid_type or duplicate_name or invalid_active or invalid_url"` 全部通过。

## T9：实现 thinking 配置语义

**文件：** `src/okcode/config.py`、`tests/unit/test_config.py`

**依赖：** T8

**步骤：**

1. `thinking` 省略时归一化为 `false`。
2. 允许 Anthropic 与 OpenAI 兼容配置使用 `thinking: true`；不在配置层按模型名或地址做供应商硬编码。
3. 覆盖 `deepseek-v4-pro`、`protocol: openai`、`thinking: true` 的有效配置，并验证 `api_key` 永不出现在对象表示中。

**验证：** `uv run pytest tests/unit/test_config.py -q -k "thinking or deepseek or repr"` 全部通过。

## T10：定义 Provider 协议与测试替身

**文件：** `src/okcode/providers/base.py`、`tests/conftest.py`、`tests/fakes.py`

**依赖：** T5、T6

**步骤：**

1. 定义 `LLMProvider.stream(messages)` 与幂等 `aclose()` Protocol。
2. 实现可记录历史并按序产出事件、异常或阻塞的 `FakeProvider`。
3. 实现记录提示、增量、错误、取消与退出状态的 `FakeTerminal`，并能观测流关闭。

**验证：** `uv run python -m compileall -q src/okcode/providers/base.py tests/fakes.py tests/conftest.py` 成功。

## T11：实现可控 SSE 测试流

**文件：** `tests/helpers/sse.py`

**依赖：** T2、T3

**步骤：**

1. 实现 `httpx.AsyncByteStream` 测试流和请求捕获 transport。
2. 支持分块释放、延迟门闩、主动断流和取消后的关闭观测。
3. 提供 OpenAI 与 Anthropic 原始 SSE 事件编码函数。

**验证：** `uv run python -m compileall -q tests/helpers/sse.py` 成功。

## T12：建立 OpenAI 客户端与历史序列化

**文件：** `src/okcode/providers/openai.py`、`tests/integration/test_openai_sse.py`

**依赖：** T6、T9、T10

**步骤：**

1. 创建 `AsyncOpenAI`，原样传入配置的 `base_url` 与 `api_key`，并设置 `max_retries=0`。
2. 为测试保留可注入客户端或 HTTP transport 的构造入口。
3. 将通用历史序列化为 Chat Completions 的 `role/content`；DeepSeek 历史不回传旧 reasoning 内容。

**验证：** `uv run pytest tests/integration/test_openai_sse.py -q -k "client_configuration or serializes_history"` 全部通过。

## T13：实现 OpenAI thinking 请求扩展

**文件：** `src/okcode/providers/openai.py`、`tests/integration/test_openai_sse.py`

**依赖：** T12

**步骤：**

1. `thinking: true` 时通过 `extra_body` 发送 `{"thinking": {"type": "enabled"}}`。
2. 不发送 `reasoning_effort`；`thinking: false` 时不发送 thinking 扩展。
3. 将不支持该扩展的服务端响应映射为统一错误，而不是回退或切换模型。

**验证：** `uv run pytest tests/integration/test_openai_sse.py -q -k "thinking_request or omits_reasoning_effort or no_thinking_extension"` 全部通过。

## T14：实现 OpenAI 可见增量转换

**文件：** `src/okcode/providers/openai.py`、`tests/integration/test_openai_sse.py`

**依赖：** T11、T13

**步骤：**

1. 使用 `stream=True` 和异步上下文消费 SDK 流。
2. 忽略空 `choices`、角色元数据及空增量。
3. 读取可选 `reasoning_content` 扩展字段并产生 `ThinkingDelta`；读取 `content` 或 refusal 并产生 `TextDelta`。
4. 同一 chunk 同时存在两类内容时，先产生思考增量再产生回答增量。

**验证：** `uv run pytest tests/integration/test_openai_sse.py -q -k "reasoning_content or text_delta or simultaneous_delta"` 全部通过。

## T15：实现 OpenAI 完整性判断

**文件：** `src/okcode/providers/openai.py`、`tests/integration/test_openai_sse.py`

**依赖：** T14

**步骤：**

1. 跟踪 index 0 的非空 `finish_reason`，但继续消费到流正常耗尽后才完成。
2. 正常耗尽且存在终止标记时只产生一个 `StreamCompleted`，其消息只保存正式回答正文。
3. 无终止标记、异常 EOF、仅思考无正式回答或多个完成事件均抛出 `STREAM` 错误。

**验证：** `uv run pytest tests/integration/test_openai_sse.py -q -k "finish_reason or incomplete_stream or answer_required or completed_once"` 全部通过。

## T16：实现 OpenAI 错误映射与关闭

**文件：** `src/okcode/providers/openai.py`、`tests/integration/test_openai_sse.py`

**依赖：** T15

**步骤：**

1. 将认证、权限、连接、超时、限流、请求与服务端 SDK 异常映射为安全 `ProviderError`。
2. 已开始消费后发生的异常映射为 `STREAM`；原样传播 `CancelledError`。
3. 通过流上下文关闭响应，实现幂等 `aclose()`，并断言 5xx 只请求一次。

**验证：** `uv run pytest tests/integration/test_openai_sse.py -q -k "error_mapping or cancellation or close or retries"` 全部通过。

## T17：建立 Anthropic 客户端与请求参数

**文件：** `src/okcode/providers/anthropic.py`、`tests/integration/test_anthropic_sse.py`

**依赖：** T6、T9、T10

**步骤：**

1. 创建 `AsyncAnthropic`，传入配置的认证与 `base_url`，并设置 `max_retries=0`。
2. 将通用历史转换为 Messages 格式；带私有状态的助手消息优先回传结构化 content blocks。
3. thinking 关闭时省略 thinking 参数；开启时使用 `max_tokens=4096` 与 `budget_tokens=1024`。

**验证：** `uv run pytest tests/integration/test_anthropic_sse.py -q -k "request_body or thinking_budget or serializes_history"` 全部通过。

## T18：实现 Anthropic 增量与完整终态

**文件：** `src/okcode/providers/anthropic.py`、`tests/integration/test_anthropic_sse.py`

**依赖：** T11、T17

**步骤：**

1. 在 `messages.stream()` 异步上下文中将 thinking、text 增量映射为统一可见事件。
2. 忽略 signature 等不可见事件，并完整消费到协议终态。
3. 调用 `get_final_message()`，要求协议终态、非空 `stop_reason` 和正式文本后才产生唯一完成事件；`max_tokens` 等合法停止原因也视为完整终态。

**验证：** `uv run pytest tests/integration/test_anthropic_sse.py -q -k "thinking_delta or text_delta or final_message or stop_reason"` 全部通过。

## T19：保存并回传 Anthropic 私有状态

**文件：** `src/okcode/providers/anthropic.py`、`tests/integration/test_anthropic_sse.py`

**依赖：** T18

**步骤：**

1. 将 final Message 的 thinking、signature、redacted thinking 与 text blocks 防御性复制为可重新提交的结构化状态。
2. 保持 block 顺序、签名和密文，不用字符串拼接或可变 SDK 对象引用。
3. 下一轮请求原样回传该状态；UI 只显示 thinking/text，不显示签名或密文。

**验证：** `uv run pytest tests/integration/test_anthropic_sse.py -q -k "provider_state or replay_blocks or signature_hidden"` 全部通过。

## T20：实现 Anthropic 错误映射与关闭

**文件：** `src/okcode/providers/anthropic.py`、`tests/integration/test_anthropic_sse.py`

**依赖：** T19

**步骤：**

1. 将流建立前连接/超时和 API 分类错误映射为安全 Provider 错误。
2. 将已开始消费后的中断、缺失终态和空正式回答映射为 `STREAM`。
3. 原样传播 `CancelledError`，关闭流和客户端，验证无自动重试且 `aclose()` 幂等。

**验证：** `uv run pytest tests/integration/test_anthropic_sse.py -q -k "incomplete_stream or error_mapping or cancellation or close or retries"` 全部通过。

## T21：实现会话成功路径与原子提交

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`

**依赖：** T5、T10

**步骤：**

1. 用“已提交历史 + 临时用户消息”快照调用 Provider。
2. 立即向调用方转发 thinking/text 增量，内部截获完成事件。
3. 只有 Provider 正常耗尽且完成事件合法时，原子追加用户与助手两条消息。

**验证：** `uv run pytest tests/unit/test_conversation.py -q -k "successful_turn or forwards_delta or atomic_commit"` 全部通过。

## T22：实现会话失败与取消回滚

**文件：** `src/okcode/conversation.py`、`tests/unit/test_conversation.py`

**依赖：** T21

**步骤：**

1. 覆盖 Provider 异常、取消、缺少/重复完成事件、完成后额外事件和空正式回答。
2. 所有失败路径均不写入临时用户或部分助手消息。
3. 取消时确保异步生成器 finally 执行，下一轮历史不含被取消轮次。

**验证：** `uv run pytest tests/unit/test_conversation.py -q -k "rollback or cancellation or invalid_stream"` 全部通过。

## T23：实现行内输入

**文件：** `src/okcode/terminal.py`、`tests/unit/test_terminal.py`

**依赖：** T2、T3

**步骤：**

1. 使用同步 `PromptSession` 和仅进程内的输入历史。
2. 输入阶段 `KeyboardInterrupt` 清空当前输入并重试；EOF 返回退出信号。
3. 不启用全屏应用或鼠标布局。

**验证：** `uv run pytest tests/unit/test_terminal.py -q -k "prompt or input_interrupt or eof"` 全部通过。

## T24：实现安全的流式终端渲染

**文件：** `src/okcode/terminal.py`、`tests/unit/test_terminal.py`

**依赖：** T5、T6、T23

**步骤：**

1. 每轮某类事件首次出现时显示一次“思考”或“回答”标签，随后立即追加文本。
2. 禁用 Rich markup 与自动高亮，保留中文、换行和 `[red]` 等模型原文。
3. 收尾、错误、取消与退出时恢复换行并重置渲染状态；不输出密钥、签名或 redacted 数据。

**验证：** `uv run pytest tests/unit/test_terminal.py -q -k "labels or immediate_output or literal_markup or safe_output"` 全部通过。

## T25：实现 REPL 成功路径与退出

**文件：** `src/okcode/app.py`、`tests/unit/test_app.py`

**依赖：** T10、T21、T24

**步骤：**

1. 实现同步 REPL，用同一个 `asyncio.Runner` 运行包装后的单轮协程。
2. 空白输入不调用 Provider；成功轮次渲染完成后进入下一次输入。
3. `/exit` 和 EOF 正常结束，不写入进程外会话状态。

**验证：** `uv run pytest tests/unit/test_app.py -q -k "successful_turn or empty_input or exit or eof"` 全部通过。

## T26：实现生成取消、错误恢复与 Runner 复用

**文件：** `src/okcode/app.py`、`tests/unit/test_app.py`

**依赖：** T16、T20、T22、T25

**步骤：**

1. Provider 与 Conversation 不捕获或包装 `CancelledError`。
2. `Runner.run()` 出现 `KeyboardInterrupt` 时显示取消提示、重置终端并回到输入。
3. ProviderError 只结束当前轮；取消或错误后用同一 Runner 成功执行下一轮。

**验证：** `uv run pytest tests/unit/test_app.py -q -k "keyboard_interrupt or provider_error or continues_after_failure"` 全部通过。

## T27：实现 Provider 工厂

**文件：** `src/okcode/providers/factory.py`、`src/okcode/providers/__init__.py`、`tests/unit/test_cli.py`

**依赖：** T16、T20

**步骤：**

1. 根据 `ProviderProtocol` 创建唯一的 OpenAI 或 Anthropic 适配器。
2. 为防御性未知协议抛出内部配置错误。
3. 保持工厂不依赖 UI 或 Conversation。

**验证：** `uv run pytest tests/unit/test_cli.py -q -k "provider_factory"` 全部通过。

## T28：实现 CLI 组装、清理与模块入口

**文件：** `src/okcode/cli.py`、`src/okcode/__main__.py`、`tests/unit/test_cli.py`

**依赖：** T9、T25、T26、T27

**步骤：**

1. 按“UI → 配置 → Provider → 会话 → Runner → App”顺序组装，配置有效前不得创建 Provider。
2. 在 finally 中用同一 Runner 调用 `provider.aclose()`，再关闭 Runner。
3. 规定正常退出码 0、配置错误 2、未处理启动错误 1；`python -m okcode` 调用相同 `main()`。

**验证：** `uv run pytest tests/unit/test_cli.py -q -k "invalid_config_no_provider or cleanup or exit_code or module_entry"` 全部通过。

## T29：补齐配置示例与使用文档

**文件：** `config.example.yaml`、`README.md`

**依赖：** T9、T28

**步骤：**

1. 提供 DeepSeek `deepseek-v4-pro` 的 OpenAI 协议示例：`thinking: true`，不写 `reasoning_effort`，只使用占位密钥。
2. 提供 Anthropic 示例，说明默认配置路径、`active` 切换、安装、启动、Ctrl+C、EOF、`/exit` 和进程内会话范围。
3. 不在文档、示例或 Git 跟踪文件写入真实密钥。

**验证：** `uv run python -c "from pathlib import Path; from okcode.config import load_config; load_config(Path('config.example.yaml'))"` 成功，且 `rg -n "sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}" .` 无匹配。

## T30：执行自动化质量门禁

**文件：** 全部源码与测试文件

**依赖：** T1-T29

**步骤：**

1. 运行全量测试，修复本阶段引入的问题。
2. 运行 Ruff 格式与 lint，修复问题但不扩展 Tool Use、持久化或 Markdown 渲染范围。
3. 构建分发包，确认 `src` 布局和入口配置可用。

**验证：** 依次运行 `uv run pytest -q`、`uv run ruff format --check .`、`uv run ruff check .`、`uv build`，预期四条命令退出码均为 0。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6
                    ├→ T7 → T8 → T9
                    ├→ T10
                    └→ T11

T6 + T9 + T10 ─→ T12 → T13 → T14 → T15 → T16
T6 + T9 + T10 ─→ T17 → T18 → T19 → T20

T5 + T10 ─→ T21 → T22
T3 ─→ T23 → T24

T10 + T21 + T24 ─→ T25 → T26
T16 + T20 ─→ T27
T9 + T25 + T26 + T27 ─→ T28 → T29

T1-T29 → T30
```

OpenAI、Anthropic、会话和终端四条分支在各自依赖满足后可以并行。真实 DeepSeek API 与 tmux 端到端验收留给 `checklist.md`，避免在验收标准未审批前提前执行付费请求。
